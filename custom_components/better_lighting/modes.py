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

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
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

        self.state: str = IDLE_STATE
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

        self.session_id = stored.session_id
        self.state = stored.state
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
        if not self.active:
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
        if state == IDLE_STATE:
            await self.async_end()
            return
        if state not in self.config.states:
            _LOGGER.warning("%s has no state %r; ignoring", self.config.name, state)
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

    async def async_end(self, *, restore: bool = True) -> None:
        """Return to idle, putting the rooms back."""
        if not self.active:
            self.state = IDLE_STATE
            return

        session_id = self.session_id or ""
        for action in self.deferred.drop_session(session_id):
            self._fire_deferred(action, "dropped", "session_ended")

        snapshot, self.snapshot = self.snapshot, None
        previous, self.state = self.state, IDLE_STATE
        self.session_id = None

        for zone_id in self.config.zone_ids:
            controller = self.controllers.get(zone_id)
            if controller is None:
                continue
            controller.release_session_owner()
            if restore:
                await self._async_restore_zone(zone_id, controller, snapshot)

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
        for zone_id in sorted(self.config.zone_ids):
            if zone_id in self.opted_out:
                continue
            controller = self.controllers.get(zone_id)
            if controller is None:
                continue
            rule = self.config.rule_for(self.state, zone_id)
            if rule is None:
                continue
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
        if rule is None or not rule.presence_entry_scene:
            return
        controller = self.controllers.get(zone_id)
        if controller is None:
            return
        await controller.async_set_mode(ZoneMode.SCENE, rule.presence_entry_scene)

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
