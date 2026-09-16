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

import asyncio
import contextlib
import datetime
import hashlib
import logging
import zoneinfo
from collections.abc import Callable
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
from homeassistant.util import dt as dt_util

from .adaptive import AdaptiveConfig, SunEventOrderError, compute_for_transition
from .const import (
    DOMAIN,
    NightBehavior,
    PresenceOffAction,
    PresenceOnAction,
    PressAction,
    RestoreOnPowerCycle,
)
from .context import ContextRegistry
from .cycle import (
    ADAPTIVE,
    OFF,
    StepKind,
    ZoneCycleState,
    scene_step,
)
from .cycle import (
    Step as CycleStep,
)
from .cycle import (
    press as cycle_press,
)
from .cycle import (
    press_previous as cycle_press_previous,
)
from .openings import WindowWatcher
from .presence import ZonePresence
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
from .util import clamp

if TYPE_CHECKING:
    from .light import ZoneLight
    from .models import ControllerConfig, HubConfig, ZoneConfig

_LOGGER = logging.getLogger(__name__)

EVENT_ZONE_MODE_CHANGED = f"{DOMAIN}_zone_mode_changed"

__all__ = ["EVENT_ZONE_MODE_CHANGED", "Trigger", "ZoneController", "ZoneMode"]

# Tolerances for "the light is already where we want it". Below these a command
# would be pure event-bus noise: many devices quantise brightness differently
# (Z-Wave uses 0-99), and colour temperature rounds hard in mired.
BRIGHTNESS_TOLERANCE = 2
MIRED_TOLERANCE = 3

# Give the tick a little room beyond the interval so a slow render cannot
# overlap the next one.
_TICK_PADDING = datetime.timedelta(seconds=0.5)

# How far a light must move, under someone else's hand, before we conclude a
# human meant it. Deliberately far looser than the "is this already right?"
# tolerances: a device rounding its own brightness must not read as a person
# reaching for the dimmer.
MANUAL_BRIGHTNESS_DELTA = 25
MANUAL_MIRED_DELTA = 20

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

        # Tracked per axis: a room can follow the sun's colour while its
        # brightness stays put, or the other way round.
        self.adapt_brightness = zone.adaptive_brightness_on
        self.adapt_color = zone.adaptive_color_on
        self.night_active = False
        # Night mode wants this room dark, but somebody is still in it.
        self._night_turn_off_pending = False
        self.mode: ZoneMode = ZoneMode.ADAPTIVE
        self.active_scene_id: str | None = None
        # A signed relative dim, in percentage points, applied on top of
        # whatever brightness source is active. Milestone 4 drives this.
        self.bias_pct = 0.0
        # Axes a human has taken over, per light. Per (zone, light) by
        # construction, because this dict belongs to one zone.
        self.manual: dict[str, Axis] = {}

        # Set while a cross-zone mode is driving this room. A press clears it,
        # which is how requirement 2's "press a switch during the film and that
        # room goes back to normal" is expressed -- and it is scoped to the
        # session, so nothing needs a timeout to expire.
        self.session_owner: tuple[str, str] | None = None
        self._opt_out_callback: Callable[[str], None] | None = None
        # Presence, for modes that gate on whether a room is occupied.
        self.presence: ZonePresence | None = None
        self.windows: WindowWatcher | None = None
        # A window is open in this room.
        self.insect_active = False
        # A press has waved insect mode away until the window closes and is
        # opened again. Scoped like every other dismissal, so it needs no timer.
        self.insect_dismissed = False
        self._pre_insect: CycleStep | None = None

        # The zone's own light entity, attached once its platform is up. It
        # owns the on-state memory, so turning the room off or back on has to
        # go through it rather than commanding members directly.
        self.light: ZoneLight | None = None

        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._listeners: list[CALLBACK_TYPE] = []
        # Where each controller last left off, for the remember-position policy.
        self._last_index: dict[str, int | None] = {}
        # The scene to resume after a power cycle, and when it was set.
        self._last_scene_id: str | None = None
        self._last_scene_at: datetime.datetime | None = None
        # What we last told each light, so a later change can be compared
        # against our intent rather than against its own previous state.
        self._last_commanded: dict[str, dict[str, Any]] = {}
        self._manual_timers: dict[str, CALLBACK_TYPE] = {}
        # Serialises everything that mutates this room. Presses, presence, a
        # cinema mode and the interval tick all arrive independently, and two
        # of them interleaving would publish a state nobody asked for.
        self._lock = asyncio.Lock()
        self._lock_owner: asyncio.Task | None = None
        self._saturation: dict[str, Saturation] = {}
        self._sun_error_logged = False

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        """Start the tick and follow the night-mode source entity."""
        if source := self.night_source:
            self.night_active = self._read_night_source(source)
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass, [source], self._handle_night_source
                )
            )

        if self.zone.lights:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass, list(self.zone.lights), self._handle_member_change
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
        for cancel in self._manual_timers.values():
            cancel()
        self._manual_timers.clear()
        self._unsubscribers.clear()
        self._listeners.clear()

    @callback
    def set_session_owner(
        self,
        mode_id: str,
        session_id: str,
        on_opt_out: Callable[[str], None] | None = None,
    ) -> None:
        """Hand this room to a cross-zone mode for the length of its session."""
        self.session_owner = (mode_id, session_id)
        self._opt_out_callback = on_opt_out

    @callback
    def release_session_owner(self) -> None:
        self.session_owner = None
        self._opt_out_callback = None

    @callback
    def attach_light(self, light: ZoneLight) -> None:
        """Register the zone's light entity once its platform has come up."""
        self.light = light

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

    @contextlib.asynccontextmanager
    async def _serialised(self):
        """Hold this zone's lock, re-entrantly.

        Re-entrant because the public entry points call each other -- a press
        resolves to a mode change which renders -- and a plain lock would
        deadlock on the second hop.
        """
        task = asyncio.current_task()
        if self._lock_owner is task:
            yield
            return
        async with self._lock:
            self._lock_owner = task
            try:
                yield
            finally:
                self._lock_owner = None

    # -- inputs ------------------------------------------------------------

    @property
    def night_source(self) -> str | None:
        """The helper this zone follows for night mode.

        House-wide, because "everyone is asleep" is a fact about the household.
        A zone configured before the helper moved to the hub keeps its own
        until the global one is set, so an upgrade changes nothing on its own.
        """
        return self.hub.night_source_entity or self.zone.night_source_entity

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
        # From the source entity, so the whole house is going to bed. That is
        # what may darken a room, unlike the zone's own night switch.
        self.hass.async_create_task(self._async_night_changed(from_source=True))

    @callback
    def _handle_tick(self, _now: datetime.datetime) -> None:
        self.hass.async_create_task(self.async_render(Trigger.TICK))

    @property
    def adaptive_axes(self) -> Axis:
        """Which axes this room still tracks the sun on."""
        axes = Axis.NONE
        if self.adapt_brightness:
            axes |= Axis.BRIGHTNESS
        if self.adapt_color:
            axes |= Axis.COLOR
        return axes

    @property
    def adaptive_enabled(self) -> bool:
        """Whether the sun still drives anything in this room."""
        return self.adaptive_axes is not Axis.NONE

    async def async_set_adaptive_axis(self, axis: Axis, enabled: bool) -> None:
        """Turn one axis of this zone's adaptive engine on or off."""
        if axis is Axis.BRIGHTNESS:
            if enabled == self.adapt_brightness:
                return
            self.adapt_brightness = enabled
        else:
            if enabled == self.adapt_color:
                return
            self.adapt_color = enabled
        self.async_notify()
        if enabled:
            await self.async_render(Trigger.ACTIVATE, only_lit=True)

    async def async_set_night(self, active: bool) -> None:
        """Set night mode from the zone's own switch.

        Deliberately never darkens the room, even when the zone is configured
        to switch off at night. Reaching for this switch is someone asking for
        night light *now*; the whole-house helper going on is the house going
        to bed, which is a different thing.
        """
        if active == self.night_active:
            return
        self.night_active = active
        self.async_notify()
        await self._async_night_changed(from_source=False)

    async def _async_night_changed(self, *, from_source: bool) -> None:
        """Apply a change of night mode."""
        if not self.night_active:
            self._night_turn_off_pending = False
            await self.async_render(Trigger.ACTIVATE, only_lit=True)
            return

        if (
            from_source
            and self.zone.night_behavior is NightBehavior.TURN_OFF
            and self._any_member_on()
        ):
            presence = self.presence
            if presence is not None and not presence.is_clear:
                # Somebody is still in here. Wait rather than switching the
                # light off from under them; presence will call back when the
                # room empties.
                self._night_turn_off_pending = True
                _LOGGER.debug(
                    "%s: night turn-off waiting for the room to empty",
                    self.zone.name,
                )
                return
            await self.async_set_mode(ZoneMode.OFF)
            return

        await self.async_render(Trigger.ACTIVATE, only_lit=True)

    async def async_set_mode(self, mode: ZoneMode, scene_id: str | None = None) -> None:
        """Change what this zone is doing, and re-render once."""
        async with self._serialised():
            await self._async_set_mode(mode, scene_id)

    async def _async_set_mode(
        self, mode: ZoneMode, scene_id: str | None = None
    ) -> None:
        if mode is ZoneMode.SCENE and scene_id not in self.scenes:
            _LOGGER.warning(
                "%s: scene %r is not defined; staying in %s",
                self.zone.name,
                scene_id,
                self.mode,
            )
            return
        previous_mode = self.mode
        self.mode = mode
        self.active_scene_id = scene_id if mode is ZoneMode.SCENE else None
        if mode is ZoneMode.SCENE and scene_id:
            self._last_scene_id = scene_id
            self._last_scene_at = dt_util.utcnow()
        # A deliberate mode change is a fresh start: any relative dim the user
        # had layered on the previous look no longer applies.
        self.bias_pct = 0.0
        self.async_notify()
        self.hass.bus.async_fire(
            EVENT_ZONE_MODE_CHANGED,
            {
                "zone_id": self.zone.subentry_id,
                "zone": self.zone.name,
                "from_mode": previous_mode.value,
                "to_mode": mode.value,
                "effective_mode": self.effective_mode.value,
                "scene_id": self.active_scene_id,
            },
        )

        if mode is ZoneMode.OFF:
            # Through the light entity, so it captures which members were on
            # before the room went dark.
            if self.light is not None:
                await self.light.async_turn_off()
                return
            await self.async_render(Trigger.ACTIVATE)
            return

        if not self._any_member_on():
            # Coming back from dark: light only the members that were on last
            # time, at the value the new mode implies, in one command.
            targets = (
                self.light.restore_targets()
                if self.light is not None
                else list(self.zone.lights)
            )
            if not await self.async_render(Trigger.TURN_ON, entity_ids=targets):
                # The engine had nothing to say -- adaptive is switched off, or
                # every light is already where it should be. Switching the
                # adaptive engine off means "stop managing my colour", not
                # "stop the light switch working", so the room still lights.
                await self._async_call(
                    "turn_on", {ATTR_ENTITY_ID: sorted(targets)}, Trigger.TURN_ON
                )
            return

        await self.async_render(Trigger.ACTIVATE)

    async def async_activate_scene(self, scene_id: str) -> None:
        await self.async_set_mode(ZoneMode.SCENE, scene_id)

    async def async_set_adaptive(self) -> None:
        """Return to plain adaptive lighting."""
        await self.async_set_mode(ZoneMode.ADAPTIVE)

    # -- presses -----------------------------------------------------------

    def _current_step(self) -> CycleStep | None:
        """The zone's current mode expressed as a cycle position."""
        mode = self.mode
        if mode is ZoneMode.OFF:
            return OFF
        if mode is ZoneMode.ADAPTIVE:
            return ADAPTIVE
        if mode is ZoneMode.SCENE and self.active_scene_id:
            return scene_step(self.active_scene_id)
        # Insect, cinema and anything else are not cycle positions. Returning
        # None makes them "foreign", which is exactly right: the next press
        # restarts the pressed controller's own list.
        return None

    def _resume_step(self) -> CycleStep | None:
        """What the first press after a power cycle should resume, if anything."""
        if self.zone.restore_on_power_cycle is not RestoreOnPowerCycle.LAST_SCENE:
            return None
        if not self._last_scene_id or self._last_scene_id not in self.scenes:
            return None
        max_age = self.zone.resume_max_age_minutes
        if max_age and self._last_scene_at is not None:
            age = dt_util.utcnow() - self._last_scene_at
            if age > datetime.timedelta(minutes=max_age):
                # Off overnight should start the next morning in adaptive,
                # not in last night's dinner scene.
                return None
        return scene_step(self._last_scene_id)

    @property
    def pending_scene_id(self) -> str | None:
        """The scene the next press would resume, if this room is off."""
        step = self._resume_step()
        if step is not None and step.kind is StepKind.SCENE:
            return step.scene_id
        return None

    def _any_member_on(self) -> bool:
        return any(
            (state := self.hass.states.get(entity_id)) is not None
            and state.state == STATE_ON
            for entity_id in self.zone.lights
        )

    def _dismiss(self) -> bool:
        """Clear whatever automatic layer is above the base intent.

        Returns whether anything was actually cleared, which is what makes the
        press land on adaptive without advancing.
        """
        dismissed = False
        if self._night_turn_off_pending:
            # Somebody reached for the switch while we were waiting to darken
            # the room. That is them saying they want it lit.
            self._night_turn_off_pending = False
        if self.manual:
            self.clear_manual()
            dismissed = True
        if self.session_owner is not None:
            # The user has reached for the switch during a film. This room
            # leaves the mode's control for the rest of the session.
            mode_id, _session_id = self.session_owner
            callback_fn, self._opt_out_callback = self._opt_out_callback, None
            self.session_owner = None
            if callback_fn is not None:
                callback_fn(mode_id)
            dismissed = True
        if self.insect_showing and self.zone.insect_overridable_by_press:
            # Waved away until the window is closed and opened again.
            self.insect_dismissed = True
            dismissed = True
        return dismissed

    async def async_press(
        self, controller: ControllerConfig, kind: str = "press", *, steps: int = 1
    ) -> None:
        """Handle a press from ``controller``. ``steps`` counts a coalesced burst."""
        async with self._serialised():
            await self._async_press(controller, kind, steps=steps)

    async def _async_press(
        self, controller: ControllerConfig, kind: str = "press", *, steps: int = 1
    ) -> None:
        action = {
            "press": PressAction.CYCLE_NEXT,
            "double_press": controller.double_press_action,
            "long_press": controller.long_press_action,
            "down_press": controller.down_press_action,
            "down_double_press": controller.down_double_press_action,
            "down_long_press": controller.down_long_press_action,
        }.get(kind, PressAction.CYCLE_NEXT)

        _LOGGER.debug(
            "%s: %s from %s -> %s", self.zone.name, kind, controller.name, action
        )

        match action:
            case PressAction.NONE:
                return
            case PressAction.CYCLE_NEXT:
                await self.async_cycle(controller, direction=1, steps=steps)
            case PressAction.CYCLE_PREVIOUS:
                await self.async_cycle(controller, direction=-1, steps=steps)
            case PressAction.RESET_ADAPTIVE:
                self._dismiss()
                await self.async_set_mode(ZoneMode.ADAPTIVE)
            case PressAction.ZONE_OFF:
                await self.async_set_mode(ZoneMode.OFF)
            case PressAction.TOGGLE_NIGHT:
                await self.async_set_night(not self.night_active)
            case PressAction.BRIGHTEN:
                await self._async_adjust_bias(controller.dim_step_pct * steps)
            case PressAction.DIM:
                await self._async_adjust_bias(-controller.dim_step_pct * steps)

    async def _async_adjust_bias(self, delta: float) -> None:
        """Shift the whole room up or down, without leaving the curve.

        A bias rather than an absolute brightness, so a room held down two
        steps keeps following the sun all evening -- two steps below where it
        would otherwise be -- instead of freezing at whatever value the hold
        happened to land on.
        """
        self.bias_pct = clamp(self.bias_pct + delta, -100.0, 100.0)
        _LOGGER.debug("%s: brightness bias now %+.0f%%", self.zone.name, self.bias_pct)
        if self.mode is ZoneMode.OFF:
            # Holding the dimmer in a dark room sets where it will come back
            # on, rather than lighting it.
            self.async_notify()
            return
        await self.async_render(Trigger.DIM, only_lit=True)
        self.async_notify()

    async def async_cycle(
        self, controller: ControllerConfig, *, direction: int = 1, steps: int = 1
    ) -> None:
        """Advance (or retreat) this zone along ``controller``'s list.

        ``steps`` greater than one comes from a burst of quick taps. They are
        resolved entirely in the pure layer and rendered **once**, so tapping
        three times moves three places without strobing the room through the
        two in between.
        """
        cycle = controller.cycle(frozenset(self.scenes))
        state = ZoneCycleState(
            current=self._current_step(),
            is_off=not self._any_member_on(),
            last_index=self._last_index.get(controller.subentry_id),
            resume=self._resume_step(),
        )
        # Only the first press of a burst can dismiss; the rest are ordinary
        # advances from wherever that landed.
        dismissed = self._dismiss() if direction > 0 else False

        result = None
        for step_number in range(max(steps, 1)):
            if direction < 0:
                result = cycle_press_previous(cycle, state)
            else:
                result = cycle_press(
                    cycle, state, dismissed=dismissed and step_number == 0
                )
            state = ZoneCycleState(
                current=result.step,
                is_off=result.step.kind is StepKind.OFF,
                last_index=result.index,
                resume=state.resume,
            )

        assert result is not None
        self._last_index[controller.subentry_id] = result.index
        _LOGGER.debug(
            "%s: %s x%d -> %s (%s)",
            self.zone.name,
            controller.name,
            steps,
            result.step,
            result.reason,
        )
        await self.async_apply_step(result.step)

    async def async_apply_step(self, step: CycleStep) -> None:
        """Put the zone into the state a cycle position describes."""
        match step.kind:
            case StepKind.OFF:
                await self.async_set_mode(ZoneMode.OFF)
            case StepKind.SCENE if step.scene_id:
                await self.async_set_mode(ZoneMode.SCENE, step.scene_id)
            case _:
                await self.async_set_mode(ZoneMode.ADAPTIVE)

    # -- presence and windows ---------------------------------------------

    def _presence_allowed(self) -> bool:
        """Whether presence has any say in this room right now.

        Three separate ways to silence it, all meaning the same thing, so the
        check is an OR rather than a precedence chain: a bedroom at night, a
        scene that says so, and a room a cross-zone mode is driving.
        """
        if self.session_owner is not None:
            # A mode is driving this room; its own presence rules apply there.
            return False
        if self.is_night and self.zone.night_ignore_presence:
            return False
        scene = self.active_scene()
        return not (scene is not None and scene.ignore_presence)

    def _presence_target(self) -> CycleStep:
        """Where presence should put the room when somebody walks in."""
        match self.zone.presence_on_action:
            case PresenceOnAction.SCENE if self.zone.presence_on_scene_id:
                return scene_step(self.zone.presence_on_scene_id)
            case PresenceOnAction.RESTORE:
                # Honour the room's own power-cycle setting, so presence and
                # the light switch agree about what "on" means here.
                return self._resume_step() or ADAPTIVE
            case _:
                return ADAPTIVE

    async def async_presence_detected(self) -> None:
        """Somebody has walked in."""
        if self.zone.presence_on_action is PresenceOnAction.NONE:
            return
        if not self._presence_allowed():
            return
        if self.presence is not None and not self.presence.covers_ok:
            # Requirement 3: only when the blinds are down.
            _LOGGER.debug("%s: presence blocked by the cover gate", self.zone.name)
            return
        if self.zone.presence_on_only_when_off and self._any_member_on():
            return
        await self.async_apply_step(self._presence_target())

    async def async_presence_cleared(self) -> None:
        """The room has emptied."""
        if self._night_turn_off_pending:
            self._night_turn_off_pending = False
            if self.night_active and self._any_member_on():
                _LOGGER.debug("%s: night turn-off released", self.zone.name)
                await self.async_set_mode(ZoneMode.OFF)
            return
        if self.zone.presence_off_action is PresenceOffAction.NONE:
            return
        if not self._presence_allowed():
            return
        if self.zone.presence_respects_manual and self.manual:
            # Somebody set this room by hand. Switching it off behind them
            # would be the rudest possible reading of an empty room.
            return
        if self.zone.presence_off_action is PresenceOffAction.ADAPTIVE:
            await self.async_set_adaptive()
            return
        await self.async_set_mode(ZoneMode.OFF)

    async def async_cover_gate_opened(self) -> None:
        """The blinds came down while somebody was already in the room."""
        await self.async_presence_detected()

    async def async_window_opened(self) -> None:
        """Requirement 4: a window is open, so switch to the insect scene."""
        if self.zone.insect_scene(self.scenes) is None:
            return
        if self.zone.insect_only_when_on and not self._any_member_on():
            # An open window is no reason to light a dark room.
            return
        self.insect_active = True
        self.insect_dismissed = False
        self._pre_insect = self._current_step()
        self.async_notify()
        await self.async_render(Trigger.ACTIVATE, only_lit=True)

    async def async_window_closed(self) -> None:
        """Put the room back to whatever it was doing before."""
        was_showing = self.insect_showing
        self.insect_active = False
        self.insect_dismissed = False
        previous, self._pre_insect = self._pre_insect, None
        self.async_notify()
        if not was_showing:
            # A press had already waved it away, so the user's choice stands.
            return
        if previous is not None:
            await self.async_apply_step(previous)
            return
        await self.async_render(Trigger.ACTIVATE)

    # -- manual override ---------------------------------------------------

    @callback
    def _handle_member_change(self, event: Event[EventStateChangedData]) -> None:
        """Notice a human moving one of our lights.

        Compared against what *we* last commanded rather than against the
        light's own previous state. Adaptive Lighting compares old to new,
        which misses three slow dimmer taps that never individually cross the
        threshold, and misfires on lights that report their own settling.
        """
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        ours = self.contexts.is_ours(event.context)
        if new_state is None or new_state.state != STATE_ON:
            if new_state is not None and new_state.state == "off":
                # Switching a light off is the universal reset.
                self.clear_manual(entity_id)
                if not ours:
                    self._reconcile_power(on=False)
            return
        if ours:
            return
        self._reconcile_power(on=True)
        if not self.hub.take_over_control:
            return

        commanded = self._last_commanded.get(entity_id)
        if not commanded:
            return
        # No separate echo window is needed here: every delta below is measured
        # against what we actually commanded, so a light merely reporting back
        # what we asked for scores zero and is not mistaken for a person. The
        # window still guards the paths that have no commanded value to
        # compare against.

        axes = Axis.NONE
        brightness = new_state.attributes.get("brightness")
        wanted_brightness = commanded.get("brightness")
        if (
            brightness is not None
            and wanted_brightness is not None
            and abs(int(brightness) - int(wanted_brightness)) > MANUAL_BRIGHTNESS_DELTA
        ):
            axes |= Axis.BRIGHTNESS

        kelvin = new_state.attributes.get("color_temp_kelvin")
        wanted_kelvin = commanded.get("color_temp_kelvin")
        if kelvin and wanted_kelvin:
            if abs(1e6 / float(kelvin) - 1e6 / float(wanted_kelvin)) > (
                MANUAL_MIRED_DELTA
            ):
                axes |= Axis.COLOR
        elif wanted_kelvin and new_state.attributes.get("color_mode") not in (
            None,
            "color_temp",
        ):
            # A scene on the bridge flipped it from white to colour. No numeric
            # threshold sees that, but the mode change itself is conclusive.
            axes |= Axis.COLOR

        if axes is not Axis.NONE:
            self.note_manual(entity_id, axes)

    @callback
    def _reconcile_power(self, *, on: bool) -> None:
        """Follow the room when somebody bypasses us.

        Switching every bulb off individually -- at the wall, from the bulb's
        own card, from another automation -- leaves the room dark while we
        still believe it is in adaptive mode. The next press then resumes the
        cycle instead of starting it, and the Scenes selector reads as though
        the room were lit. Watching the members keeps our idea of the room and
        the room itself in step.
        """
        if on:
            if self.mode is ZoneMode.OFF:
                # Somebody lit one by hand. Recorded, but not re-rendered:
                # touching the room now would undo what they just set. The
                # next tick adapts it, as it would any light we own.
                self.mode = ZoneMode.ADAPTIVE
                self.async_notify()
            return
        if self.mode is ZoneMode.OFF or self._any_member_on():
            return
        self.hass.async_create_task(self.async_set_mode(ZoneMode.OFF))

    @callback
    def note_manual(self, entity_id: str, axes: Axis) -> None:
        """Record that a human owns these axes of this light, for now."""
        current = self.manual.get(entity_id, Axis.NONE)
        if axes in current:
            return
        self.manual[entity_id] = current | axes
        _LOGGER.debug(
            "%s: %s taken over manually (%s)", self.zone.name, entity_id, axes
        )
        self._arm_manual_reset(entity_id)
        self.async_notify()

    @callback
    def _arm_manual_reset(self, entity_id: str) -> None:
        if (cancel := self._manual_timers.pop(entity_id, None)) is not None:
            cancel()
        seconds = self.hub.autoreset_manual_seconds
        if not seconds:
            return

        @callback
        def _reset(_now: datetime.datetime) -> None:
            self._manual_timers.pop(entity_id, None)
            if self.manual.pop(entity_id, None) is not None:
                _LOGGER.debug(
                    "%s: %s handed back to the engine", self.zone.name, entity_id
                )
                self.async_notify()
                self.hass.async_create_task(self.async_render(Trigger.ACTIVATE))

        self._manual_timers[entity_id] = async_call_later(self.hass, seconds, _reset)

    @callback
    def clear_manual(self, entity_id: str | None = None) -> None:
        """Hand control back to the engine."""
        targets = [entity_id] if entity_id else list(self.manual)
        for target in targets:
            self.manual.pop(target, None)
            if (cancel := self._manual_timers.pop(target, None)) is not None:
                cancel()
        if targets:
            self.async_notify()

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
        if self.insect_showing:
            # An open window is a physical fact about *this* room, while a
            # cross-zone mode is a house-wide preference, so the window wins.
            return ZoneMode.INSECT
        if self.is_night and self.mode is ZoneMode.ADAPTIVE:
            return ZoneMode.NIGHT
        return self.mode

    @property
    def insect_showing(self) -> bool:
        """Whether insect mode is currently what this room should look like."""
        return (
            self.insect_active
            and not self.insect_dismissed
            and self.zone.insect_scene(self.scenes) is not None
        )

    def active_scene(self) -> Scene | None:
        """The scene the current mode resolves to, if any."""
        mode = self.effective_mode
        if mode is ZoneMode.INSECT:
            return self.zone.insect_scene(self.scenes)
        if mode is ZoneMode.SCENE:
            return self.scenes.get(self.active_scene_id or "")
        if mode is ZoneMode.NIGHT:
            # Only the "apply a scene" behaviour resolves to one. "Minimum
            # settings" is handled inside the curve, and "switch off" leaves
            # the room dark, so neither has a scene.
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
        self,
        trigger: Trigger,
        entity_ids: list[str] | None = None,
        *,
        only_lit: bool = False,
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
                adaptive_axes=self.adaptive_axes,
                transition=self._transition_for(trigger),
                only_lit=only_lit,
            )
        )
        self._saturation = result.saturation
        return result.commands

    async def async_render(
        self,
        trigger: Trigger = Trigger.TICK,
        *,
        entity_ids: list[str] | None = None,
        only_lit: bool = False,
    ) -> bool:
        """Decide and send. Returns whether anything was actually issued."""
        async with self._serialised():
            commands = [
                command
                for command in self.commands_for(trigger, entity_ids, only_lit=only_lit)
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
        if action == "turn_on":
            payload = {
                key: value for key, value in data.items() if key != ATTR_ENTITY_ID
            }
            for entity_id in entity_ids:
                self._last_commanded[entity_id] = payload
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
