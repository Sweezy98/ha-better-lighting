"""The per-zone runtime: the adaptive tick, and the dispatch of its results.

One :class:`ZoneController` per zone subentry. It owns that zone's adaptive
state and is the only thing that commands the zone's member lights. There is
deliberately no shared manager: Adaptive Lighting keeps manual-control state,
timers and last-sent data in one global object keyed by light entity alone, and
its own source comments record the cross-talk that causes when two profiles
share a light. Here every fact is per-zone by construction.

Milestone 2 covers the adaptive path. The mode/scene state machine, controller
presses and manual-override detection arrive in milestone 4; the hooks they need
are marked below.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import zoneinfo
from enum import StrEnum
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
    SERVICE_TURN_ON,
    STATE_ON,
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
from .profiles import (
    IDENTITY_PROFILE,
    Axis,
    LightCapabilities,
    LightProfile,
    LightTarget,
    Saturation,
    resolve_target,
)

if TYPE_CHECKING:
    from .models import HubConfig, ZoneConfig

_LOGGER = logging.getLogger(__name__)

# Tolerances for "the light is already where we want it". Below these a command
# would be pure event-bus noise: brightness is quantised differently by many
# devices (Z-Wave uses 0-99), and colour temperature rounds hard in mired.
BRIGHTNESS_TOLERANCE = 2
MIRED_TOLERANCE = 3

# Give the tick a little room beyond the interval so a slow render cannot
# overlap the next one.
_TICK_PADDING = datetime.timedelta(seconds=0.5)


class Trigger(StrEnum):
    """Why a render is happening. Determines which axes may be emitted."""

    # Mode change, zone turned on, manual reset: make one visible move.
    ACTIVATE = "activate"
    # The periodic refresh: adjust what is already lit, never switch anything.
    TICK = "tick"
    # A member went from off to on and needs catching up.
    TURN_ON = "turn_on"
    # A service call or diagnostic asked for it explicitly.
    FORCE = "force"


class ZoneController:
    """Owns one zone's adaptive behaviour."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone: ZoneConfig,
        hub: HubConfig,
        contexts: ContextRegistry,
        profiles: dict[str, LightProfile] | None = None,
    ) -> None:
        self.hass = hass
        self.zone = zone
        self.hub = hub
        self.contexts = contexts
        self.profiles = profiles or {}

        self.adaptive_enabled = zone.adaptive_default_on
        self.night_active = False

        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._listeners: list[CALLBACK_TYPE] = []
        self._last_sent: dict[str, dict[str, Any]] = {}
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
        offset = interval.total_seconds() * fraction

        @callback
        def _start(_now: datetime.datetime) -> None:
            self._unsubscribers.append(
                async_track_time_interval(self.hass, self._handle_tick, interval)
            )

        self._unsubscribers.append(async_call_later(self.hass, offset, _start))

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
        if new_state is None:
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
            # Catch up immediately rather than waiting for the next interval.
            await self.async_render(Trigger.ACTIVATE)

    async def async_set_night(self, active: bool) -> None:
        """Set night mode directly, for zones with no source entity."""
        if active == self.night_active:
            return
        self.night_active = active
        self.async_notify()
        await self.async_render(Trigger.ACTIVATE)

    # -- rendering ---------------------------------------------------------

    @property
    def is_night(self) -> bool:
        """Whether night settings apply, honouring the zone's night behaviour."""
        if self.zone.night_behavior is NightBehavior.OFF:
            return False
        return self.night_active

    def adaptive_config(self) -> AdaptiveConfig:
        location: astral.Location = get_astral_location(self.hass)[0]
        timezone = dt_timezone(self.hass)
        return self.zone.adaptive_config(self.hub, location.observer, timezone)

    def _transition_for(self, trigger: Trigger) -> float:
        if trigger is Trigger.TURN_ON:
            return self.hub.initial_transition
        if self.is_night:
            return self.zone.night_transition
        return self.zone.effective_transition(self.hub)

    def _capabilities(self, entity_id: str) -> LightCapabilities | None:
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0
        return LightCapabilities.from_attributes(
            entity_id,
            state.attributes,
            supports_transition=bool(features & LightEntityFeature.TRANSITION),
        )

    def _is_redundant(self, entity_id: str, data: dict[str, Any]) -> bool:
        """True when the light is already showing this, within tolerance.

        Without this the interval tick republishes every light every time, which
        is pure recorder churn and can visibly re-trigger transitions.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state != STATE_ON:
            return False

        for key, value in data.items():
            if key == ATTR_TRANSITION:
                continue
            current = state.attributes.get(key)
            if current is None:
                return False
            if key == "brightness":
                if abs(int(current) - int(value)) > BRIGHTNESS_TOLERANCE:
                    return False
            elif key == "color_temp_kelvin":
                # Compare in mired: a fixed Kelvin tolerance is far too tight at
                # the warm end and far too loose at the cold end.
                if abs(1e6 / float(current) - 1e6 / float(value)) > MIRED_TOLERANCE:
                    return False
            elif tuple(current) != tuple(value):
                return False
        return True

    def targets_for(
        self, entity_ids: list[str], *, want: Axis = Axis.ALL, bias_pct: float = 0.0
    ) -> list[LightTarget]:
        """Resolve the adaptive curve into a concrete target per light."""
        try:
            settings = compute_for_transition(
                self.adaptive_config(),
                self._transition_for(Trigger.TICK),
                is_night=self.is_night,
            )
        except SunEventOrderError as err:
            # Log once per configuration, not once per interval: Adaptive
            # Lighting spams this every 30 seconds when a user's offset is bad.
            if not self._sun_error_logged:
                _LOGGER.error("%s: %s", self.zone.name, err)
                self._sun_error_logged = True
            return []
        self._sun_error_logged = False

        targets = []
        for entity_id in entity_ids:
            caps = self._capabilities(entity_id)
            if caps is None:
                continue
            profile = self.profiles.get(entity_id, IDENTITY_PROFILE)
            target = resolve_target(
                settings, profile, caps, want=want, bias_pct=bias_pct
            )
            if target.saturation:
                self._saturation[entity_id] = target.saturation
            else:
                self._saturation.pop(entity_id, None)
            if not target.is_empty:
                targets.append(target)
        return targets

    async def async_render(
        self, trigger: Trigger = Trigger.TICK, *, entity_ids: list[str] | None = None
    ) -> None:
        """Push the adaptive curve to this zone's lights."""
        if not self.adaptive_enabled:
            return

        candidates = entity_ids if entity_ids is not None else list(self.zone.lights)
        if trigger is Trigger.TICK:
            # Invariant: a tick adjusts what is lit and never changes on/off.
            candidates = [
                entity_id
                for entity_id in candidates
                if (state := self.hass.states.get(entity_id)) is not None
                and state.state == STATE_ON
            ]
        if not candidates:
            return

        targets = self.targets_for(candidates)
        transition = self._transition_for(trigger)
        payloads: dict[tuple, list[str]] = {}

        for target in targets:
            data = target.as_service_data()
            if self._is_redundant(target.entity_id, data):
                continue
            caps = self._capabilities(target.entity_id)
            if transition and caps is not None and caps.supports_transition:
                data[ATTR_TRANSITION] = transition
            self._last_sent[target.entity_id] = data
            payloads.setdefault(_freeze(data), []).append(target.entity_id)

        for frozen, ids in payloads.items():
            await self._async_call(dict(frozen), ids, trigger)

    async def _async_call(
        self, data: dict[str, Any], entity_ids: list[str], trigger: Trigger
    ) -> None:
        context = self.contexts.new_context(self.zone.subentry_id, str(trigger))
        for entity_id in entity_ids:
            self.contexts.note_command(entity_id)
        _LOGGER.debug(
            "%s %s -> %s %s", self.zone.name, trigger, sorted(entity_ids), data
        )
        await self.hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {**data, ATTR_ENTITY_ID: sorted(entity_ids)},
            blocking=True,
            context=context,
        )

    # -- diagnostics -------------------------------------------------------

    @property
    def saturated_lights(self) -> dict[str, Saturation]:
        """Lights whose calibration is being clipped, for entity attributes."""
        return dict(self._saturation)


def _freeze(data: dict[str, Any]) -> tuple:
    """A hashable form of a service payload, so identical ones can be batched."""
    return tuple(
        sorted(
            (key, tuple(value) if isinstance(value, list | tuple) else value)
            for key, value in data.items()
        )
    )


def dt_timezone(hass: HomeAssistant) -> datetime.tzinfo:
    """Home Assistant's configured timezone."""
    return zoneinfo.ZoneInfo(hass.config.time_zone)
