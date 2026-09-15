"""The per-zone runtime: mode, the adaptive tick, and dispatch.

One :class:`ZoneController` per zone subentry. It owns that zone's mode and is
the only thing that commands the zone's member lights. There is deliberately no
shared manager: Adaptive Lighting keeps manual-control state, timers and
last-sent data in one global object keyed by light entity alone, and its own
source comments record the cross-talk that causes when two profiles share a
light. Here every fact is per-zone by construction.

The controller decides *when* and *to whom*; :mod:`.render` decides *what*.
Keeping those apart is what makes the whole behaviour matrix testable without
an event loop.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import zoneinfo
from typing import TYPE_CHECKING, Any

import astral
from homeassistant.components.light import (
    ATTR_TRANSITION,
    LightEntityFeature,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.sun import get_astral_location

from .adaptive import AdaptiveConfig, SunEventOrderError, compute_for_transition
from .const import NightBehavior
from .context import ContextRegistry
from .profiles import Axis, LightCapabilities, LightProfile, Saturation
from .render import (
    LightCommand,
    LightSnapshot,
    RenderRequest,
    Trigger,
    ZoneMode,
    batch,
    render_zone,
)
from .scenes import Scene

if TYPE_CHECKING:
    from .models import HubConfig, ZoneConfig

_LOGGER = logging.getLogger(__name__)

__all__ = ["Trigger", "ZoneController", "ZoneMode"]

# Tolerances for "the light is already where we want it". Below these a command
# would be pure event-bus noise: many devices quantise brightness differently
# (Z-Wave uses 0-99), and colour temperature rounds hard in mired.
BRIGHTNESS_TOLERANCE = 2
MIRED_TOLERANCE = 3

# Give the tick a little room beyond the interval so a slow render cannot
# overlap the next one.
_TICK_PADDING = datetime.timedelta(seconds=0.5)

_VALID_MEMBER_STATES = (STATE_ON, "off")


class ZoneController:
    """Owns one zone's mode and drives its lights."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone: ZoneConfig,
        hub: HubConfig,
        contexts: ContextRegistry,
        profiles: dict[str, LightProfile] | None = None,
        scenes: dict[str, Scene] | None = None,
    ) -> None:
        self.hass = hass
        self.zone = zone
        self.hub = hub
        self.contexts = contexts
        self.profiles = profiles or {}
        self.scenes = scenes or {}

        self.adaptive_enabled = zone.adaptive_default_on
        self.night_active = False
        self.mode: ZoneMode = ZoneMode.ADAPTIVE
        self.active_scene_id: str | None = None
        # A signed relative dim, in percentage points, applied on top of
        # whatever brightness source is active. Milestone 4 drives this.
        self.bias_pct = 0.0
        # Axes a human has taken over, per light. Per (zone, light) by
        # construction, because this dict belongs to one zone.
        self.manual: dict[str, Axis] = {}

        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._listeners: list[CALLBACK_TYPE] = []
        self._saturation: dict[str, Saturation] = {}
        self._sun_error_logged = False

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        """Start the tick and follow the night-mode source entity."""
        if source := self.zone.night_source_entity:
            self.night_active = self._read_night_source(source)
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass, [source], self._handle_night_source
                )
            )

        interval = (
            datetime.timedelta(seconds=self.zone.effective_interval(self.hub))
            + _TICK_PADDING
        )

        # Stagger zones deterministically so twenty rooms do not all render in
        # the same event-loop slot every interval.
        digest = hashlib.sha256(self.zone.subentry_id.encode()).digest()
        fraction = int.from_bytes(digest[:8], "big") / 2**64

        @callback
        def _start(_now: datetime.datetime) -> None:
            self._unsubscribers.append(
                async_track_time_interval(self.hass, self._handle_tick, interval)
            )

        self._unsubscribers.append(
            async_call_later(self.hass, interval.total_seconds() * fraction, _start)
        )

    @callback
    def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._listeners.clear()

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Subscribe an entity to this zone's state, returning an unsubscribe."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def async_notify(self) -> None:
        """Tell subscribed entities that something they display has changed."""
        for listener in list(self._listeners):
            listener()

    # -- inputs ------------------------------------------------------------

    def _read_night_source(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == STATE_ON

    @callback
    def _handle_night_source(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        active = new_state.state == STATE_ON
        if active == self.night_active:
            return
        self.night_active = active
        _LOGGER.debug("%s night mode -> %s", self.zone.name, active)
        self.async_notify()
        self.hass.async_create_task(self.async_render(Trigger.ACTIVATE))

    @callback
    def _handle_tick(self, _now: datetime.datetime) -> None:
        self.hass.async_create_task(self.async_render(Trigger.TICK))

    async def async_set_adaptive_enabled(self, enabled: bool) -> None:
        """Turn this zone's adaptive engine on or off."""
        if enabled == self.adaptive_enabled:
            return
        self.adaptive_enabled = enabled
        self.async_notify()
        if enabled:
            await self.async_render(Trigger.ACTIVATE)

    async def async_set_night(self, active: bool) -> None:
        """Set night mode directly, for zones with no source entity."""
        if active == self.night_active:
            return
        self.night_active = active
        self.async_notify()
        await self.async_render(Trigger.ACTIVATE)

    async def async_set_mode(self, mode: ZoneMode, scene_id: str | None = None) -> None:
        """Change what this zone is doing, and re-render once."""
        if mode is ZoneMode.SCENE and scene_id not in self.scenes:
            _LOGGER.warning(
                "%s: scene %r is not defined; staying in %s",
                self.zone.name,
                scene_id,
                self.mode,
            )
            return
        self.mode = mode
        self.active_scene_id = scene_id if mode is ZoneMode.SCENE else None
        # A deliberate mode change is a fresh start: any relative dim the user
        # had layered on the previous look no longer applies.
        self.bias_pct = 0.0
        self.async_notify()
        await self.async_render(Trigger.ACTIVATE)

    async def async_activate_scene(self, scene_id: str) -> None:
        await self.async_set_mode(ZoneMode.SCENE, scene_id)

    async def async_set_adaptive(self) -> None:
        """Return to plain adaptive lighting."""
        await self.async_set_mode(ZoneMode.ADAPTIVE)

    # -- state -------------------------------------------------------------

    @property
    def is_night(self) -> bool:
        """Whether night settings apply, honouring the zone's night behaviour."""
        if self.zone.night_behavior is NightBehavior.OFF:
            return False
        return self.night_active

    @property
    def effective_mode(self) -> ZoneMode:
        """The mode as rendered, with night folded in.

        Night is a modifier rather than a mode of its own: it either warms and
        dims the curve, or swaps in a designated scene. Either way the render
        pipeline sees one mode and one optional scene.
        """
        if self.mode is ZoneMode.OFF:
            return ZoneMode.OFF
        if self.is_night and self.mode is ZoneMode.ADAPTIVE:
            return ZoneMode.NIGHT
        return self.mode

    def active_scene(self) -> Scene | None:
        """The scene the current mode resolves to, if any."""
        mode = self.effective_mode
        if mode is ZoneMode.SCENE:
            return self.scenes.get(self.active_scene_id or "")
        if mode is ZoneMode.NIGHT:
            # Only the "apply a scene" behaviour resolves to one; the
            # "minimum settings" behaviour is handled inside the curve.
            if self.zone.night_behavior is NightBehavior.SCENE:
                return self.scenes.get(self.zone.night_scene_id or "")
            return None
        return None

    def adaptive_config(self) -> AdaptiveConfig:
        location: astral.Location = get_astral_location(self.hass)[0]
        timezone = zoneinfo.ZoneInfo(self.hass.config.time_zone)
        return self.zone.adaptive_config(self.hub, location.observer, timezone)

    def _transition_for(self, trigger: Trigger) -> float:
        if trigger is Trigger.TURN_ON:
            return self.hub.initial_transition
        if trigger is Trigger.ACTIVATE:
            return (
                self.zone.night_transition
                if self.is_night
                else self.hub.scene_transition
            )
        return self.zone.effective_transition(self.hub)

    def _snapshot(self, entity_id: str) -> LightSnapshot | None:
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0
        return LightSnapshot(
            entity_id=entity_id,
            is_on=state.state == STATE_ON,
            available=state.state in _VALID_MEMBER_STATES,
            caps=LightCapabilities.from_attributes(
                entity_id,
                state.attributes,
                supports_transition=bool(features & LightEntityFeature.TRANSITION),
            ),
            brightness=state.attributes.get("brightness"),
            attributes=state.attributes,
        )

    # -- rendering ---------------------------------------------------------

    def commands_for(
        self, trigger: Trigger, entity_ids: list[str] | None = None
    ) -> list[LightCommand]:
        """Decide what this zone's lights should do, without sending anything."""
        if not self.adaptive_enabled and self.effective_mode is not ZoneMode.SCENE:
            # Adaptive is off and nothing else is driving: leave the lights be.
            return []

        candidates = entity_ids if entity_ids is not None else list(self.zone.lights)
        members = [
            snapshot
            for entity_id in candidates
            if (snapshot := self._snapshot(entity_id)) is not None
        ]
        if not members:
            return []

        try:
            settings = compute_for_transition(
                self.adaptive_config(),
                self._transition_for(trigger),
                is_night=self.is_night,
            )
        except SunEventOrderError as err:
            # Once per configuration, not once per tick: Adaptive Lighting logs
            # this every interval when a user's offset is bad.
            if not self._sun_error_logged:
                _LOGGER.error("%s: %s", self.zone.name, err)
                self._sun_error_logged = True
            return []
        self._sun_error_logged = False

        result = render_zone(
            RenderRequest(
                mode=self.effective_mode,
                trigger=trigger,
                settings=settings,
                members=members,
                scene=self.active_scene(),
                profiles=self.profiles,
                manual=self.manual,
                bias_pct=self.bias_pct,
                transition=self._transition_for(trigger),
            )
        )
        self._saturation = result.saturation
        return result.commands

    async def async_render(
        self, trigger: Trigger = Trigger.TICK, *, entity_ids: list[str] | None = None
    ) -> bool:
        """Decide and send. Returns whether anything was actually issued."""
        commands = [
            command
            for command in self.commands_for(trigger, entity_ids)
            if not self._is_redundant(command)
        ]
        if not commands:
            return False

        for action, data in batch(commands):
            await self._async_call(action, data, trigger)
        return True

    def _is_redundant(self, command: LightCommand) -> bool:
        """True when the light is already doing this, within tolerance.

        Without this the interval republishes every light every time, which is
        recorder churn and can visibly restart a transition.
        """
        state = self.hass.states.get(command.entity_id)
        if state is None:
            return False
        if command.action == "turn_off":
            return state.state != STATE_ON
        if state.state != STATE_ON:
            return False

        for key, value in command.data.items():
            if key == ATTR_TRANSITION:
                continue
            current = state.attributes.get(key)
            if current is None:
                return False
            if key == "brightness":
                if abs(int(current) - int(value)) > BRIGHTNESS_TOLERANCE:
                    return False
            elif key == "color_temp_kelvin":
                # Compare in mired: a fixed Kelvin tolerance is far too tight
                # at the warm end and far too loose at the cold end.
                if abs(1e6 / float(current) - 1e6 / float(value)) > MIRED_TOLERANCE:
                    return False
            elif tuple(current) != tuple(value):
                return False
        return True

    async def _async_call(
        self, action: str, data: dict[str, Any], trigger: Trigger
    ) -> None:
        entity_ids = data[ATTR_ENTITY_ID]
        context = self.contexts.new_context(self.zone.subentry_id, str(trigger))
        for entity_id in entity_ids:
            self.contexts.note_command(entity_id)
        _LOGGER.debug("%s %s %s -> %s", self.zone.name, trigger, action, data)
        await self.hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON if action == "turn_on" else SERVICE_TURN_OFF,
            data,
            blocking=True,
            context=context,
        )

    # -- diagnostics -------------------------------------------------------

    @property
    def saturated_lights(self) -> dict[str, Saturation]:
        """Lights whose calibration is being clipped, for entity attributes."""
        return dict(self._saturation)
