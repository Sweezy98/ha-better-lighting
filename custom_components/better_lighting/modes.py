"""Cross-zone modes: the home-cinema case.

An external automation moves the mode between its states -- playing, paused,
credits -- and each (state, room) pair has a rule. The hard parts are not the
happy path:

* A room with somebody in it is not plunged into darkness. The instruction is
  deferred until the room empties, and re-checked when it fires, because by
  then the session may have ended, the film may have been paused, or the user
  may have taken the room back.
* The snapshot is taken **once**, at the start of the session, and survives
  every state change within it. Re-snapshotting on "paused" would capture the
  film-watching state and the final restore would relight nothing.
* On the way out, rooms return to *adaptive*, filtered to the lights that were
  on beforehand. The snapshot says which lights; the curve says how bright.
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.util import ulid as ulid_util

from .const import DOMAIN, IDLE_STATE
from .render import Trigger, ZoneMode
from .session import (
    DeferredAction,
    DeferredRegistry,
    LightSnapshotEntry,
    ModeSnapshot,
    OptedOutOnExit,
    RestoreMode,
    ZoneAction,
    ZoneSnapshot,
    is_still_wanted,
)
from .store import PersistedSession, SessionStore

if TYPE_CHECKING:
    from .models import ModeConfig, ModeRule
    from .zone import ZoneController

_LOGGER = logging.getLogger(__name__)

EVENT_MODE_CHANGED = f"{DOMAIN}_mode_changed"
EVENT_ZONE_OPTED_OUT = f"{DOMAIN}_zone_opted_out"
EVENT_DEFERRED = f"{DOMAIN}_deferred_action"

# One shared sweeper rather than a timer per action: fewer handles, no
# cancellation bookkeeping, and minute-level resolution on a multi-hour expiry
# is neither here nor there.
SWEEP_INTERVAL = 60


class ModeGroupRuntime:
    """One cross-zone mode, and the session it is currently running."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: ModeConfig,
        controllers: dict[str, ZoneController],
        deferred: DeferredRegistry,
        store: SessionStore | None = None,
    ) -> None:
        self.hass = hass
        self.config = config
        self.controllers = controllers
        self.deferred = deferred
        self.store = store

        # What the driving automation last told us, whether or not we are
        # acting on it. Kept separately from the session so that enabling the
        # mode halfway through a film knows the film is playing -- automations
        # fire on *changes*, so there would otherwise be nothing to go on
        # until the next one.
        self.state: str = IDLE_STATE
        self.enabled = True
        self.session_id: str | None = None
        self.snapshot: ModeSnapshot | None = None
        self.opted_out: set[str] = set()

        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._listeners: list[CALLBACK_TYPE] = []

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        await self._async_restore()
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass,
                self._handle_sweep,
                datetime.timedelta(seconds=SWEEP_INTERVAL),
            )
        )

    async def _async_restore(self) -> None:
        """Pick a session back up after a restart or a reload.

        The rules are re-applied rather than replayed: every command goes
        through the same redundancy filter as any other render, so a room that
        is already dark receives nothing. What the re-application actually
        achieves is re-claiming ownership of each room and re-queueing a
        deferred turn-off for any room that is still occupied.
        """
        if self.store is None:
            return
        stored = self.store.get(self.config.subentry_id)
        if stored is None:
            return
        if stored.state not in self.config.states:
            # The mode has been reconfigured since. Better to drop the session
            # than to resume one whose states no longer exist.
            self.store.drop(self.config.subentry_id)
            return

        self.state = stored.state
        if not stored.session_id:
            # Tracked while switched off. Nothing to resume, but the state
            # survives the restart so switching on still knows where we are.
            self.async_notify()
            return
        self.session_id = stored.session_id
        self.opted_out = set(stored.opted_out)
        self.snapshot = stored.snapshot
        _LOGGER.debug(
            "%s: resumed session %s in %s",
            self.config.name,
            self.session_id,
            self.state,
        )
        await self._async_apply_state()
        self.async_notify()

    def _persist(self) -> None:
        if self.store is None:
            return
        if not self.active and self.state == IDLE_STATE:
            self.store.drop(self.config.subentry_id)
            return
        self.store.put(
            PersistedSession(
                mode_id=self.config.subentry_id,
                session_id=self.session_id or "",
                state=self.state,
                opted_out=sorted(self.opted_out),
                snapshot=self.snapshot,
            )
        )

    @callback
    def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._listeners.clear()

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def async_notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @property
    def active(self) -> bool:
        return self.session_id is not None

    # -- state changes -----------------------------------------------------

    async def async_set_state(self, state: str) -> None:
        """Move the mode to ``state``. The idle state ends the session."""
        if state == self.state:
            return
        if state != IDLE_STATE and state not in self.config.states:
            _LOGGER.warning("%s has no state %r; ignoring", self.config.name, state)
            return

        if not self.enabled:
            # Switched off, so nothing happens to the rooms -- but the state is
            # still recorded, so switching it on mid-film picks up from there.
            previous, self.state = self.state, state
            self._persist()
            self.async_notify()
            _LOGGER.debug(
                "%s is switched off; noted %s without acting", self.config.name, state
            )
            self.hass.bus.async_fire(
                EVENT_MODE_CHANGED,
                {
                    "mode_id": self.config.subentry_id,
                    "mode": self.config.name,
                    "from_state": previous,
                    "to_state": state,
                    "session_id": None,
                    "applied": False,
                },
            )
            return

        if state == IDLE_STATE:
            await self.async_end()
            return

        starting = not self.active
        if starting:
            self.session_id = ulid_util.ulid_now()
            self.opted_out = set()
            if self.config.snapshot_on_enter:
                self.snapshot = self._take_snapshot()
        else:
            # The mode moved on. Anything the previous state was waiting to do
            # no longer reflects anybody's intention; the new state decides
            # afresh below.
            for action in self.deferred.drop_session(self.session_id or ""):
                self._fire_deferred(action, "dropped", "state_changed")

        previous, self.state = self.state, state
        await self._async_apply_state()
        self._persist()
        self.async_notify()
        self.hass.bus.async_fire(
            EVENT_MODE_CHANGED,
            {
                "mode_id": self.config.subentry_id,
                "mode": self.config.name,
                "from_state": previous,
                "to_state": state,
                "session_id": self.session_id,
                "started": starting,
            },
        )

    async def async_end(
        self, *, restore: bool = True, reset_state: bool = True
    ) -> None:
        """End the session, putting the rooms back.

        ``reset_state`` is False when the mode is being switched off rather
        than the film ending: the film is still playing, so the state stays
        recorded even though we stop acting on it.
        """
        if not self.active:
            if reset_state:
                self.state = IDLE_STATE
            return

        session_id = self.session_id or ""
        for action in self.deferred.drop_session(session_id):
            self._fire_deferred(action, "dropped", "session_ended")

        snapshot, self.snapshot = self.snapshot, None
        previous = self.state
        if reset_state:
            self.state = IDLE_STATE
        self.session_id = None

        ran: set[ModeRule] = set()
        for rule in self.config.house_rules(IDLE_STATE):
            ran.add(rule)
            await self._async_run_scripts(rule)
        for zone_id in sorted(self.config.zone_ids):
            controller = self.controllers.get(zone_id)
            if controller is None:
                continue
            controller.release_session_owner()
            if restore:
                await self._async_restore_zone(zone_id, controller, snapshot)
            # The rooms go back to how they were; a rule for the idle state is
            # how everything else does. Run even when the mode was switched
            # off part-way through rather than the film ending, since the
            # house is being put back to rights either way.
            rule = self.config.rule_for(IDLE_STATE, zone_id)
            if rule is not None and rule not in ran:
                ran.add(rule)
                await self._async_run_scripts(rule)

        self.opted_out = set()
        self._persist()
        self.async_notify()
        self.hass.bus.async_fire(
            EVENT_MODE_CHANGED,
            {
                "mode_id": self.config.subentry_id,
                "mode": self.config.name,
                "from_state": previous,
                "to_state": IDLE_STATE,
                "session_id": session_id,
                "ended": True,
            },
        )

    # -- applying a state --------------------------------------------------

    async def _async_apply_state(self) -> None:
        ran: set[ModeRule] = set()
        # The rules that name no room: scripts for the house rather than
        # lights for a room, and nothing below would ever reach them.
        for rule in self.config.house_rules(self.state):
            ran.add(rule)
            await self._async_run_scripts(rule)

        for zone_id in sorted(self.config.zone_ids):
            if zone_id in self.opted_out:
                continue
            controller = self.controllers.get(zone_id)
            if controller is None:
                continue
            rule = self.config.rule_for(self.state, zone_id)
            if rule is None:
                continue
            # One rule can govern several rooms, and its scripts are the
            # mode's business rather than any one room's: running them once
            # per room would start the amplifier three times.
            if rule not in ran:
                ran.add(rule)
                await self._async_run_scripts(rule)
            await self._async_apply_rule(zone_id, controller, rule)

    async def _async_apply_rule(
        self, zone_id: str, controller: ZoneController, rule: ModeRule
    ) -> None:
        # Claim the room for this session, so a press on its switch takes it
        # back rather than being overwritten by the next state change.
        controller.set_session_owner(
            self.config.subentry_id,
            self.session_id or "",
            self._handle_opt_out(zone_id),
        )

        match rule.action:
            case ZoneAction.KEEP:
                return
            case ZoneAction.ADAPTIVE:
                await controller.async_set_adaptive()
            case ZoneAction.APPLY_SCENE if rule.scene_id:
                await controller.async_set_mode(ZoneMode.SCENE, rule.scene_id)
            case ZoneAction.TURN_OFF:
                await self._async_turn_off(zone_id, controller, rule)

    async def _async_run_scripts(self, rule: ModeRule) -> None:
        """Whatever else this rule does to the house.

        Fired rather than awaited: a script that dims for thirty seconds, or
        waits for a door, must not hold up the room next to it. Deliberately
        in no context of ours -- a script is somebody's own instruction, and
        anything it turns on should be read as exactly that rather than
        mistaken for our own command coming back.
        """
        if not rule.scripts:
            return
        _LOGGER.debug("%s: running %s", self.config.name, ", ".join(rule.scripts))

        async def _run() -> None:
            try:
                await self.hass.services.async_call(
                    "script",
                    "turn_on",
                    {"entity_id": list(rule.scripts)},
                    blocking=False,
                )
            except (ServiceNotFound, vol.Invalid) as err:
                # A script the user has since deleted, most likely. Worth
                # saying once; not worth an unhandled task exception every
                # time the film starts, and certainly not worth stopping the
                # rest of the mode.
                _LOGGER.warning(
                    "%s could not run %s: %s",
                    self.config.name,
                    ", ".join(rule.scripts),
                    err,
                )

        self.hass.async_create_task(_run())

    async def _async_turn_off(
        self, zone_id: str, controller: ZoneController, rule: ModeRule
    ) -> None:
        """Darken a room -- unless somebody is in it."""
        presence = controller.presence
        occupied = presence.occupied if presence is not None else None

        if rule.respect_presence and occupied and rule.defer_if_occupied:
            ttl = self.config.deferred_ttl_minutes
            action = DeferredAction(
                zone_id=zone_id,
                session_id=self.session_id or "",
                mode_id=self.config.subentry_id,
                mode_state=self.state,
                action=ZoneAction.TURN_OFF,
                created_at=time.monotonic(),
                expires_at=(time.monotonic() + ttl * 60) if ttl else None,
            )
            self.deferred.enqueue(action)
            self._fire_deferred(action, "queued", "occupied")
            # Deliberately no ownership claim beyond the one above and no
            # command: the person still in the room keeps their light exactly
            # as it was.
            return

        await controller.async_set_mode(ZoneMode.OFF)

    # -- switched on or off ------------------------------------------------

    async def async_set_enabled(self, enabled: bool) -> None:
        """Switch this mode on or off without the automation knowing.

        Switching it on part-way through picks up whatever state was last
        recorded, which is the whole reason the state is tracked while off.
        """
        if enabled == self.enabled:
            return
        self.enabled = enabled
        _LOGGER.debug("%s switched %s", self.config.name, "on" if enabled else "off")

        if not enabled:
            # Put the rooms back, but keep remembering what the film is doing.
            await self.async_end(restore=True, reset_state=False)
            self.async_notify()
            return

        self._persist()
        self.async_notify()
        if self.state != IDLE_STATE:
            # Re-apply from the top: a fresh session, snapshotting the rooms
            # as they are now, which is what they should return to.
            state, self.state = self.state, IDLE_STATE
            await self.async_set_state(state)

    # -- presence ----------------------------------------------------------

    async def async_zone_cleared(self, zone_id: str) -> None:
        """The room has emptied. Anything waiting on that can happen now."""
        if not self.active:
            return
        action = self.deferred.get(self.session_id or "", zone_id)
        controller = self.controllers.get(zone_id)
        if controller is None:
            return

        if action is not None:
            wanted, reason = is_still_wanted(
                action,
                active_session_id=self.session_id,
                active_state=self.state,
                opted_out=self.opted_out,
                zone_is_off=not controller._any_member_on(),
                zone_is_manual=bool(controller.manual),
                now=time.monotonic(),
            )
            self.deferred.pop(action.session_id, action.zone_id)
            if not wanted:
                self._fire_deferred(action, "dropped", reason)
                return
            self._fire_deferred(action, "fired", "cleared")
            await controller.async_set_mode(ZoneMode.OFF)
            return

        # No deferred action: this is somebody who walked in mid-session and
        # has now left again.
        rule = self.config.rule_for(self.state, zone_id)
        if rule is None or zone_id in self.opted_out:
            return
        if rule.on_free_action == "turn_off":
            await controller.async_set_mode(ZoneMode.OFF)
        elif rule.on_free_action == "reapply_mode_action":
            await self._async_apply_rule(zone_id, controller, rule)

    async def async_zone_occupied(self, zone_id: str) -> None:
        """Somebody has walked into a room during the session."""
        if not self.active or zone_id in self.opted_out:
            return
        rule = self.config.rule_for(self.state, zone_id)
        if rule is None:
            return
        controller = self.controllers.get(zone_id)
        if controller is None:
            return

        match rule.presence_entry_action:
            case ZoneAction.KEEP:
                return
            case ZoneAction.ADAPTIVE:
                await controller.async_set_adaptive()
            case ZoneAction.APPLY_SCENE if rule.presence_entry_scene:
                await controller.async_set_mode(
                    ZoneMode.SCENE, rule.presence_entry_scene
                )
            case ZoneAction.TURN_OFF:
                # No deferral here: they are demonstrably in the room, so
                # waiting for it to empty would mean waiting forever.
                await controller.async_set_mode(ZoneMode.OFF)

    # -- opting out --------------------------------------------------------

    def _handle_opt_out(self, zone_id: str):
        @callback
        def _opted_out(_mode_id: str) -> None:
            self.async_opt_out(zone_id, "press")

        return _opted_out

    @callback
    def async_opt_out(self, zone_id: str, cause: str) -> None:
        """This room leaves the mode's control for the rest of the session."""
        if not self.active or zone_id in self.opted_out:
            return
        self.opted_out.add(zone_id)
        for action in self.deferred.drop_zone(zone_id):
            self._fire_deferred(action, "dropped", "opted_out")
        if self.snapshot is not None and zone_id in self.snapshot.zones:
            # The user has already chosen what this room should look like;
            # putting it back at the end would undo that choice.
            self.snapshot.zones[zone_id].restore_on_exit = False
        self._persist()
        self.async_notify()
        self.hass.bus.async_fire(
            EVENT_ZONE_OPTED_OUT,
            {
                "mode_id": self.config.subentry_id,
                "zone_id": zone_id,
                "session_id": self.session_id,
                "cause": cause,
            },
        )

    # -- snapshots ---------------------------------------------------------

    def _take_snapshot(self) -> ModeSnapshot:
        snapshot = ModeSnapshot(
            session_id=self.session_id or "",
            mode_id=self.config.subentry_id,
            taken_at=dt_util.utcnow().isoformat(),
        )
        for zone_id in self.config.zone_ids:
            controller = self.controllers.get(zone_id)
            if controller is None:
                continue
            lights = []
            for entity_id in controller.zone.lights:
                state = self.hass.states.get(entity_id)
                if state is None:
                    continue
                lights.append(
                    LightSnapshotEntry(
                        entity_id=entity_id,
                        is_on=state.state == "on",
                        brightness=state.attributes.get("brightness"),
                        color=_colour_of(state.attributes),
                    )
                )
            snapshot.zones[zone_id] = ZoneSnapshot(
                zone_id=zone_id,
                mode=controller.mode.value,
                scene_id=controller.active_scene_id,
                lights=tuple(lights),
            )
        return snapshot

    async def _async_restore_zone(
        self,
        zone_id: str,
        controller: ZoneController,
        snapshot: ModeSnapshot | None,
    ) -> None:
        zone_snapshot = snapshot.zones.get(zone_id) if snapshot else None

        if zone_id in self.opted_out or (
            zone_snapshot is not None and not zone_snapshot.restore_on_exit
        ):
            match self.config.opted_out_on_exit:
                case OptedOutOnExit.KEEP:
                    return
                case OptedOutOnExit.ADAPTIVE:
                    await controller.async_set_adaptive()
                    return
                case OptedOutOnExit.RESTORE:
                    pass

        if self.config.restore_mode is RestoreMode.NONE or zone_snapshot is None:
            return

        previously_on = zone_snapshot.previously_on
        if not previously_on:
            await controller.async_set_mode(ZoneMode.OFF)
            return

        controller.mode = ZoneMode.ADAPTIVE
        controller.active_scene_id = None
        controller.async_notify()
        # The snapshot decides *which* lights come back; the curve decides how
        # bright. Replaying stored brightness would be corrected a tick later.
        await controller.async_render(Trigger.TURN_ON, entity_ids=sorted(previously_on))
        dark = [
            entity_id
            for entity_id in controller.zone.lights
            if entity_id not in previously_on
        ]
        if dark:
            await controller._async_call(
                "turn_off", {"entity_id": sorted(dark)}, Trigger.ACTIVATE
            )

    # -- housekeeping ------------------------------------------------------

    @callback
    def _handle_sweep(self, _now: datetime.datetime) -> None:
        for action in self.deferred.drop_expired(time.monotonic()):
            self._fire_deferred(action, "dropped", "expired")

    def _fire_deferred(self, action: DeferredAction, phase: str, reason: str) -> None:
        _LOGGER.debug(
            "%s: deferred %s for %s (%s)",
            self.config.name,
            phase,
            action.zone_id,
            reason,
        )
        self.hass.bus.async_fire(
            EVENT_DEFERRED,
            {
                "mode_id": action.mode_id,
                "zone_id": action.zone_id,
                "session_id": action.session_id,
                "phase": phase,
                "reason": reason,
            },
        )


_COLOUR_KEYS = (
    "color_temp_kelvin",
    "hs_color",
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
    "xy_color",
)


def _colour_of(attributes: dict[str, Any]) -> dict[str, Any] | None:
    for key in _COLOUR_KEYS:
        if (value := attributes.get(key)) is not None:
            return {key: value}
    return None
