"""The per-room runtime: mode, the adaptive tick, and dispatch.

One :class:`RoomController` per room subentry. It owns that room's mode and is
the only thing that commands the room's member lights. There is deliberately no
shared manager: Adaptive Lighting keeps manual-control state, timers and
last-sent data in one global object keyed by light entity alone, and its own
source comments record the cross-talk that causes when two profiles share a
light. Here every fact is per-room by construction.

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
    RoomCycleState,
    StepKind,
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
from .effects import EffectRequest
from .effects import frames as effect_frames
from .effects import resolve as resolve_effect
from .openings import WindowWatcher
from .presence import RoomPresence
from .profiles import Axis, LightCapabilities, LightProfile, Saturation
from .render import (
    LightCommand,
    LightSnapshot,
    RenderRequest,
    RoomMode,
    Trigger,
    batch,
    render_room,
)
from .scenes import Scene
from .scripts import async_run_scripts
from .util import clamp

if TYPE_CHECKING:
    from .light import RoomLight
    from .models import ControllerConfig, HubConfig, RoomConfig

_LOGGER = logging.getLogger(__name__)

EVENT_ROOM_MODE_CHANGED = f"{DOMAIN}_zone_mode_changed"

__all__ = ["EVENT_ROOM_MODE_CHANGED", "RoomController", "RoomMode", "Trigger"]

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


class RoomController:
    """Owns one room's mode and drives its lights."""

    def __init__(
        self,
        hass: HomeAssistant,
        room: RoomConfig,
        hub: HubConfig,
        contexts: ContextRegistry,
        profiles: dict[str, LightProfile] | None = None,
        scenes: dict[str, Scene] | None = None,
    ) -> None:
        self.hass = hass
        self.room = room
        self.hub = hub
        self.contexts = contexts
        self.profiles = profiles or {}
        self.scenes = scenes or {}

        # Tracked per axis: a room can follow the sun's colour while its
        # brightness stays put, or the other way round.
        self.adapt_brightness = room.adaptive_brightness_on
        self.adapt_color = room.adaptive_color_on
        self.night_active = False
        # A running effect: the timer for its next frame, which lights it is
        # playing on, and which of those were off when it started.
        self._effect_cancel: CALLBACK_TYPE | None = None
        self._effect_frames = None
        self._effect_request: EffectRequest | None = None
        self._effect_targets: tuple[str, ...] = ()
        self._effect_was_on: dict[str, bool] = {}
        # Night mode wants this room dark, but somebody is still in it.
        self._night_turn_off_pending = False
        self.mode: RoomMode = RoomMode.ADAPTIVE
        self.active_scene_id: str | None = None
        # A signed relative dim, in percentage points, applied on top of
        # whatever brightness source is active. Milestone 4 drives this.
        self.bias_pct = 0.0
        # Axes a human has taken over, per light. Per (room, light) by
        # construction, because this dict belongs to one room.
        self.manual: dict[str, Axis] = {}

        # Set while a cross-room mode is driving this room. A press clears it,
        # which is how requirement 2's "press a switch during the film and that
        # room goes back to normal" is expressed -- and it is scoped to the
        # session, so nothing needs a timeout to expire.
        self.session_owner: tuple[str, str] | None = None
        self._opt_out_callback: Callable[[str], None] | None = None
        # Presence, for modes that gate on whether a room is occupied.
        self.presence: RoomPresence | None = None
        self.windows: WindowWatcher | None = None
        # A window is open in this room.
        self.insect_active = False
        # A press has waved insect mode away until the window closes and is
        # opened again. Scoped like every other dismissal, so it needs no timer.
        self.insect_dismissed = False
        self._pre_insect: CycleStep | None = None

        # The room's own light entity, attached once its platform is up. It
        # owns the on-state memory, so turning the room off or back on has to
        # go through it rather than commanding members directly.
        self.light: RoomLight | None = None

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

        if self.room.lights:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass, list(self.room.lights), self._handle_member_change
                )
            )

        interval = (
            datetime.timedelta(seconds=self.room.effective_interval(self.hub))
            + _TICK_PADDING
        )

        # Stagger rooms deterministically so twenty rooms do not all render in
        # the same event-loop slot every interval.
        digest = hashlib.sha256(self.room.subentry_id.encode()).digest()
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
        # A notification part-way through outlives a reload otherwise, and
        # comes back to a room that no longer exists.
        if self._effect_cancel is not None:
            self._effect_cancel()
            self._effect_cancel = None
        self._effect_frames = None
        self._effect_request = None
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
        """Hand this room to a cross-room mode for the length of its session."""
        self.session_owner = (mode_id, session_id)
        self._opt_out_callback = on_opt_out

    @callback
    def release_session_owner(self) -> None:
        self.session_owner = None
        self._opt_out_callback = None

    @callback
    def attach_light(self, light: RoomLight) -> None:
        """Register the room's light entity once its platform has come up."""
        self.light = light

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Subscribe an entity to this room's state, returning an unsubscribe."""
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
        """Hold this room's lock, re-entrantly.

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
        """The helper this room follows for night mode.

        House-wide, because "everyone is asleep" is a fact about the household.
        A room configured before the helper moved to the hub keeps its own
        until the global one is set, so an upgrade changes nothing on its own.
        """
        return self.hub.night_source_entity or self.room.night_source_entity

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
        _LOGGER.debug("%s night mode -> %s", self.room.name, active)
        self.async_notify()
        # From the source entity, so the whole house is going to bed. That is
        # what may darken a room, unlike the room's own night switch.
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
        """Turn one axis of this room's adaptive engine on or off."""
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
        """Set night mode from the room's own switch.

        Deliberately never darkens the room, even when the room is configured
        to switch off at night. Reaching for this switch is someone asking for
        night light *now*; the whole-house helper going on is the house going
        to bed, which is a different thing.
        """
        if active == self.night_active:
            return
        self.night_active = active
        self.async_notify()
        await self._async_night_changed(from_source=False)

    async def async_request_night_off(self) -> bool:
        """Ask again for what night mode asked for when it came on.

        The house goes to bed and the rooms that should go dark go dark. Then
        somebody gets up for a glass of water, puts a light on, and goes back
        to bed -- and the night switch is no help, because night mode never
        stopped being on. Nothing had changed, so nothing happened.

        This is the way to ask a second time. Only while night mode is on:
        it is not a way to switch the lights off, it is a way to repeat
        something that already happened, and outside the night it would mean
        nothing. Down the same path as the original, so a room somebody is
        standing in is left alone exactly as it was the first time.
        """
        if not self.night_active:
            return False
        await self._async_night_changed(from_source=True)
        return True

    async def _async_night_changed(self, *, from_source: bool) -> None:
        """Apply a change of night mode."""
        if not self.night_active:
            self._night_turn_off_pending = False
            await self.async_render(Trigger.ACTIVATE, only_lit=True)
            return

        if (
            from_source
            and self.room.night_behavior is NightBehavior.TURN_OFF
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
                    self.room.name,
                )
                return
            await self.async_set_mode(RoomMode.OFF)
            return

        await self.async_render(Trigger.ACTIVATE, only_lit=True)

    async def async_set_mode(self, mode: RoomMode, scene_id: str | None = None) -> None:
        """Change what this room is doing, and re-render once."""
        async with self._serialised():
            await self._async_set_mode(mode, scene_id)

    async def _async_set_mode(
        self, mode: RoomMode, scene_id: str | None = None
    ) -> None:
        if mode is RoomMode.SCENE and scene_id not in self.scenes:
            _LOGGER.warning(
                "%s: scene %r is not defined; staying in %s",
                self.room.name,
                scene_id,
                self.mode,
            )
            return
        previous_mode = self.mode
        previous_scene_id = self.active_scene_id
        self.mode = mode
        self.active_scene_id = scene_id if mode is RoomMode.SCENE else None
        # A scene is not only its lights. Leaving one and entering another is
        # both things, in that order, and re-applying the same scene is
        # neither.
        if previous_scene_id != self.active_scene_id:
            if (leaving := self.scenes.get(previous_scene_id or "")) is not None:
                async_run_scripts(
                    self.hass, leaving.leave_scripts, f"{leaving.name} (leaving)"
                )
            if (entering := self.scenes.get(self.active_scene_id or "")) is not None:
                async_run_scripts(self.hass, entering.enter_scripts, entering.name)
        # A scene's effect belongs to the scene, so it starts and stops with
        # it -- and anything else the room does stops it too.
        if self.effect_playing:
            await self.async_stop_effect(restore=False)
        if mode is RoomMode.SCENE and scene_id:
            self._last_scene_id = scene_id
            self._last_scene_at = dt_util.utcnow()
        # A deliberate mode change is a fresh start: any relative dim the user
        # had layered on the previous look no longer applies.
        self.bias_pct = 0.0
        self.async_notify()
        self.hass.bus.async_fire(
            EVENT_ROOM_MODE_CHANGED,
            {
                # Both spellings: automations written against the old
                # word keep working, new ones read "room_id".
                "room_id": self.room.subentry_id,
                "room": self.room.name,
                "zone_id": self.room.subentry_id,
                "zone": self.room.name,
                "from_mode": previous_mode.value,
                "to_mode": mode.value,
                "effective_mode": self.effective_mode.value,
                "scene_id": self.active_scene_id,
            },
        )

        if mode is RoomMode.OFF:
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
                else list(self.room.lights)
            )
            if not await self.async_render(Trigger.TURN_ON, entity_ids=targets):
                # The engine had nothing to say -- adaptive is switched off, or
                # every light is already where it should be. Switching the
                # adaptive engine off means "stop managing my colour", not
                # "stop the light switch working", so the room still lights.
                await self._async_call(
                    "turn_on", {ATTR_ENTITY_ID: sorted(targets)}, Trigger.TURN_ON
                )
            await self._async_start_scene_effect()
            return

        await self.async_render(Trigger.ACTIVATE)
        await self._async_start_scene_effect()

    async def _async_start_scene_effect(self) -> None:
        """Play the current scene's effect, if it asked for one.

        After the render rather than instead of it: the scene decides the
        colour and which lights are lit, and the effect is what they then do
        with that.
        """
        scene = self.scenes.get(self.active_scene_id or "")
        effect = resolve_effect(scene.effect_id if scene else None, self.hub.effects)
        if scene is None or effect is None:
            return
        named = [light for light in scene.lights if light in self.room.lights]
        await self.async_play_effect(
            EffectRequest(effect=effect, brightness_pct=100.0),
            named or None,
            # Leaving the scene is what puts the room back, and it knows
            # where back is; a snapshot taken here would only fight it.
            restore=False,
        )

    async def async_activate_scene(self, scene_id: str) -> None:
        await self.async_set_mode(RoomMode.SCENE, scene_id)

    # -- effects -----------------------------------------------------------

    @property
    def effect_playing(self) -> bool:
        return self._effect_cancel is not None

    async def async_play_effect(
        self,
        request: EffectRequest,
        entity_ids: list[str] | None = None,
        *,
        restore: bool = True,
    ) -> None:
        """Play an effect on this room, or on some of its lights.

        ``restore`` remembers which of them were off, so a notification on a
        dark room leaves it dark afterwards rather than lighting it for good.
        A scene's own effect does not restore: leaving the scene is what puts
        the room back, and it has its own idea of where back is.
        """
        await self.async_stop_effect(restore=False)
        targets = [
            entity_id
            for entity_id in (entity_ids or self.room.lights)
            if entity_id in self.room.lights
        ]
        if not targets:
            return
        self._effect_targets = tuple(targets)
        self._effect_was_on = (
            {
                entity_id: (state := self.hass.states.get(entity_id)) is not None
                and state.state == STATE_ON
                for entity_id in targets
            }
            if restore
            else {}
        )
        self._effect_request = request
        self._effect_frames = iter(effect_frames(request))
        await self._async_effect_frame()

    async def _async_effect_frame(self) -> None:
        """Send one frame and book the next."""
        request = self._effect_request
        if request is None:
            return
        frame = next(self._effect_frames, None)
        if frame is None:
            # A repeating effect with no duration runs until stopped, so the
            # cycle simply starts again.
            if request.duration or not request.effect.repeat:
                await self.async_stop_effect()
                return
            self._effect_frames = iter(effect_frames(request))
            frame = next(self._effect_frames, None)
            if frame is None:
                await self.async_stop_effect()
                return

        data: dict[str, Any] = {
            ATTR_ENTITY_ID: list(self._effect_targets),
            "brightness_pct": round(frame.brightness_pct, 1),
        }
        if frame.color:
            data.update(frame.color)
        if frame.transition:
            data[ATTR_TRANSITION] = frame.transition
        await self._async_call("turn_on", data, Trigger.ACTIVATE)

        @callback
        def _next(_now: datetime.datetime) -> None:
            self._effect_cancel = None
            self.hass.async_create_task(self._async_effect_frame())

        self._effect_cancel = async_call_later(self.hass, frame.wait, _next)

    async def async_stop_effect(self, *, restore: bool = True) -> None:
        """Stop whatever is playing and put the room back."""
        if self._effect_cancel is not None:
            self._effect_cancel()
            self._effect_cancel = None
        self._effect_frames = None
        self._effect_request = None
        was_on, self._effect_was_on = self._effect_was_on, {}
        targets, self._effect_targets = self._effect_targets, ()
        if not restore or not targets:
            return

        dark = [entity_id for entity_id, lit in was_on.items() if not lit]
        if dark:
            await self._async_call("turn_off", {ATTR_ENTITY_ID: dark}, Trigger.ACTIVATE)
        # And the rest back to whatever the room was doing before.
        await self.async_render(Trigger.ACTIVATE, only_lit=True)

    async def async_set_adaptive(self) -> None:
        """Return to plain adaptive lighting."""
        await self.async_set_mode(RoomMode.ADAPTIVE)

    # -- presses -----------------------------------------------------------

    def _current_step(self) -> CycleStep | None:
        """The room's current mode expressed as a cycle position."""
        mode = self.mode
        if mode is RoomMode.OFF:
            return OFF
        if mode is RoomMode.ADAPTIVE:
            return ADAPTIVE
        if mode is RoomMode.SCENE and self.active_scene_id:
            return scene_step(self.active_scene_id)
        # Insect, cinema and anything else are not cycle positions. Returning
        # None makes them "foreign", which is exactly right: the next press
        # restarts the pressed controller's own list.
        return None

    def _resume_step(self) -> CycleStep | None:
        """What the first press after a power cycle should resume, if anything."""
        if self.room.restore_on_power_cycle is not RestoreOnPowerCycle.LAST_SCENE:
            return None
        if not self._last_scene_id or self._last_scene_id not in self.scenes:
            return None
        max_age = self.room.resume_max_age_minutes
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
            for entity_id in self.room.lights
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
        if self.insect_showing and self.room.insect_overridable_by_press:
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
            "%s: %s from %s -> %s", self.room.name, kind, controller.name, action
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
                await self.async_set_mode(RoomMode.ADAPTIVE)
            case PressAction.ROOM_OFF:
                await self.async_set_mode(RoomMode.OFF)
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
        _LOGGER.debug("%s: brightness bias now %+.0f%%", self.room.name, self.bias_pct)
        if self.mode is RoomMode.OFF:
            # Holding the dimmer in a dark room sets where it will come back
            # on, rather than lighting it.
            self.async_notify()
            return
        await self.async_render(Trigger.DIM, only_lit=True)
        self.async_notify()

    async def async_cycle(
        self, controller: ControllerConfig, *, direction: int = 1, steps: int = 1
    ) -> None:
        """Advance (or retreat) this room along ``controller``'s list.

        ``steps`` greater than one comes from a burst of quick taps. They are
        resolved entirely in the pure layer and rendered **once**, so tapping
        three times moves three places without strobing the room through the
        two in between.
        """
        cycle = controller.cycle(frozenset(self.scenes))
        state = RoomCycleState(
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
            state = RoomCycleState(
                current=result.step,
                is_off=result.step.kind is StepKind.OFF,
                last_index=result.index,
                resume=state.resume,
            )

        assert result is not None
        self._last_index[controller.subentry_id] = result.index
        _LOGGER.debug(
            "%s: %s x%d -> %s (%s)",
            self.room.name,
            controller.name,
            steps,
            result.step,
            result.reason,
        )
        await self.async_apply_step(result.step)

    async def async_apply_step(self, step: CycleStep) -> None:
        """Put the room into the state a cycle position describes."""
        match step.kind:
            case StepKind.OFF:
                await self.async_set_mode(RoomMode.OFF)
            case StepKind.SCENE if step.scene_id:
                await self.async_set_mode(RoomMode.SCENE, step.scene_id)
            case _:
                await self.async_set_mode(RoomMode.ADAPTIVE)

    # -- presence and windows ---------------------------------------------

    def _presence_allowed(self) -> bool:
        """Whether presence has any say in this room right now.

        Three separate ways to silence it, all meaning the same thing, so the
        check is an OR rather than a precedence chain: a bedroom at night, a
        scene that says so, and a room a cross-room mode is driving.
        """
        if self.session_owner is not None:
            # A mode is driving this room; its own presence rules apply there.
            return False
        if self.is_night and self.room.night_ignore_presence:
            return False
        scene = self.active_scene()
        return not (scene is not None and scene.ignore_presence)

    def _presence_target(self) -> CycleStep:
        """Where presence should put the room when somebody walks in."""
        match self.room.presence_on_action:
            case PresenceOnAction.SCENE if self.room.presence_on_scene_id:
                return scene_step(self.room.presence_on_scene_id)
            case PresenceOnAction.RESTORE:
                # Honour the room's own power-cycle setting, so presence and
                # the light switch agree about what "on" means here.
                return self._resume_step() or ADAPTIVE
            case _:
                return ADAPTIVE

    async def async_presence_detected(self) -> None:
        """Somebody has walked in."""
        if self.room.presence_on_action is PresenceOnAction.NONE:
            return
        if not self._presence_allowed():
            return
        if self.presence is not None and not self.presence.covers_ok:
            # Requirement 3: only when the blinds are down.
            _LOGGER.debug("%s: presence blocked by the cover gate", self.room.name)
            return
        if self.room.presence_on_only_when_off and self._any_member_on():
            return
        await self.async_apply_step(self._presence_target())

    async def async_presence_cleared(self) -> None:
        """The room has emptied."""
        if self._night_turn_off_pending:
            self._night_turn_off_pending = False
            if self.night_active and self._any_member_on():
                _LOGGER.debug("%s: night turn-off released", self.room.name)
                await self.async_set_mode(RoomMode.OFF)
            return
        if self.room.presence_off_action is PresenceOffAction.NONE:
            return
        if not self._presence_allowed():
            return
        if self.room.presence_respects_manual and self.manual:
            # Somebody set this room by hand. Switching it off behind them
            # would be the rudest possible reading of an empty room.
            return
        if self.room.presence_off_action is PresenceOffAction.ADAPTIVE:
            await self.async_set_adaptive()
            return
        await self.async_set_mode(RoomMode.OFF)

    async def async_cover_gate_opened(self) -> None:
        """The blinds came down while somebody was already in the room."""
        await self.async_presence_detected()

    async def async_window_opened(self) -> None:
        """Requirement 4: a window is open, so switch to the insect scene."""
        if self.room.insect_scene(self.scenes) is None:
            return
        if self.room.insect_only_when_on and not self._any_member_on():
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
            if self.mode is RoomMode.OFF:
                # Somebody lit one by hand. Recorded, but not re-rendered:
                # touching the room now would undo what they just set. The
                # next tick adapts it, as it would any light we own.
                self.mode = RoomMode.ADAPTIVE
                self.async_notify()
            return
        if self.mode is RoomMode.OFF or self._any_member_on():
            return
        self.hass.async_create_task(self.async_set_mode(RoomMode.OFF))

    @callback
    def note_manual(self, entity_id: str, axes: Axis) -> None:
        """Record that a human owns these axes of this light, for now."""
        current = self.manual.get(entity_id, Axis.NONE)
        if axes in current:
            return
        self.manual[entity_id] = current | axes
        _LOGGER.debug(
            "%s: %s taken over manually (%s)", self.room.name, entity_id, axes
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
                    "%s: %s handed back to the engine", self.room.name, entity_id
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
        """Whether night settings apply, honouring the room's night behaviour."""
        if self.room.night_behavior is NightBehavior.OFF:
            return False
        return self.night_active

    @property
    def effective_mode(self) -> RoomMode:
        """The mode as rendered, with night folded in.

        Night is a modifier rather than a mode of its own: it either warms and
        dims the curve, or swaps in a designated scene. Either way the render
        pipeline sees one mode and one optional scene.
        """
        if self.mode is RoomMode.OFF:
            return RoomMode.OFF
        if self.insect_showing:
            # An open window is a physical fact about *this* room, while a
            # cross-room mode is a house-wide preference, so the window wins.
            return RoomMode.INSECT
        if self.is_night and self.mode is RoomMode.ADAPTIVE:
            return RoomMode.NIGHT
        return self.mode

    @property
    def insect_showing(self) -> bool:
        """Whether insect mode is currently what this room should look like."""
        return (
            self.insect_active
            and not self.insect_dismissed
            and self.room.insect_scene(self.scenes) is not None
        )

    def active_scene(self) -> Scene | None:
        """The scene the current mode resolves to, if any."""
        mode = self.effective_mode
        if mode is RoomMode.INSECT:
            return self.room.insect_scene(self.scenes)
        if mode is RoomMode.SCENE:
            return self.scenes.get(self.active_scene_id or "")
        if mode is RoomMode.NIGHT:
            # Only the "apply a scene" behaviour resolves to one. "Minimum
            # settings" is handled inside the curve, and "switch off" leaves
            # the room dark, so neither has a scene.
            if self.room.night_behavior is NightBehavior.SCENE:
                return self.scenes.get(self.room.night_scene_id or "")
            return None
        return None

    def adaptive_config(self) -> AdaptiveConfig:
        location: astral.Location = get_astral_location(self.hass)[0]
        timezone = zoneinfo.ZoneInfo(self.hass.config.time_zone)
        return self.room.adaptive_config(self.hub, location.observer, timezone)

    def _transition_for(self, trigger: Trigger) -> float:
        if trigger is Trigger.TURN_ON:
            return self.hub.initial_transition
        if trigger is Trigger.ACTIVATE:
            return (
                self.room.night_transition
                if self.is_night
                else self.hub.scene_transition
            )
        return self.room.effective_transition(self.hub)

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
        """Decide what this room's lights should do, without sending anything."""
        if not self.adaptive_enabled and self.effective_mode is not RoomMode.SCENE:
            # Adaptive is off and nothing else is driving: leave the lights be.
            return []
        if trigger is Trigger.TICK and self._effect_cancel is not None:
            # Something is playing on these lights. The interval would step on
            # it every ninety seconds, which looks like the effect stuttering.
            return []

        candidates = entity_ids if entity_ids is not None else list(self.room.lights)
        if self.effective_mode is RoomMode.ADAPTIVE:
            # A room's default is "the lights, adaptively" -- and which lights
            # that means is the room's to say. Everything else it holds is
            # left for a scene to ask for by name.
            candidates = [
                entity_id for entity_id in candidates if self.room.adapts(entity_id)
            ]
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
                _LOGGER.error("%s: %s", self.room.name, err)
                self._sun_error_logged = True
            return []
        self._sun_error_logged = False

        result = render_room(
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
        context = self.contexts.new_context(self.room.subentry_id, str(trigger))
        for entity_id in entity_ids:
            self.contexts.note_command(entity_id)
        if action == "turn_on":
            payload = {
                key: value for key, value in data.items() if key != ATTR_ENTITY_ID
            }
            for entity_id in entity_ids:
                self._last_commanded[entity_id] = payload
        _LOGGER.debug("%s %s %s -> %s", self.room.name, trigger, action, data)
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
