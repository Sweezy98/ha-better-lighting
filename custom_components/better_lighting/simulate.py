"""Running a presence simulation against a real house.

The decisions live in :mod:`simulation`, which is pure and tested without a
Home Assistant in sight. This is the adapter: it watches the away helper,
asks the recorder what each room did last week, and puts the answers on a
timer.

Three things it is careful about, because all three are ways to advertise an
empty house rather than hide one:

* **It stops the moment anybody is home.** Not after the current step, not at
  the end of the timeline -- the away helper going off is the end of it, and
  every room it lit goes back to being off.
* **It never fights the user.** A room somebody has taken over by hand is left
  alone, the same way the automatic turn-off stands down.
* **It replays rooms, not bulbs.** The recorded history is of our own room
  light entity, so a replayed evening goes back through the same render
  pipeline as a real one -- calibration, groups and all -- rather than being
  restored as raw attributes.
"""

from __future__ import annotations

import datetime
import logging
import random
from dataclasses import dataclass, field

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .render import RoomMode
from .simulation import (
    RecordedState,
    ReplayStep,
    SimulationMode,
    build_timeline,
    window_for,
)
from .triggers import UNREADABLE

_LOGGER = logging.getLogger(__name__)

EVENT_SIMULATION = f"{DOMAIN}_simulation"

# What counts as "the house is empty".
_AWAY_STATES = ("on", "not_home", "away")


@dataclass
class _RoomRun:
    """One room's part in the current simulation."""

    steps: tuple[ReplayStep, ...] = ()
    started: datetime.datetime | None = None
    cancel: CALLBACK_TYPE | None = None
    at: int = 0
    lit: bool = False


@dataclass
class SimulationRunner:
    """Watches the away helper and drives the rooms while nobody is home."""

    hass: object
    runtime: object
    _unsubscribe: CALLBACK_TYPE | None = None
    _runs: dict[str, _RoomRun] = field(default_factory=dict)
    _running: bool = False
    _forced: bool = False
    _listeners: list[CALLBACK_TYPE] = field(default_factory=list)

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        entity_id = self.runtime.hub.away_entity
        if not entity_id:
            return
        self._unsubscribe = async_track_state_change_event(
            self.hass, [entity_id], self._handle_away_change
        )
        # A restart while the house is already empty should pick up where it
        # left off rather than wait for somebody to come home first.
        if self._house_is_empty():
            await self.async_start(reason="already away at startup")

    @callback
    def async_shutdown(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for run in self._runs.values():
            if run.cancel is not None:
                run.cancel()
        self._runs.clear()
        self._running = False
        self._listeners.clear()

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(listener)

        def _drop() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _drop

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # -- state -------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def rooms_running(self) -> frozenset[str]:
        return frozenset(self._runs)

    def _house_is_empty(self) -> bool:
        entity_id = self.runtime.hub.away_entity
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in UNREADABLE:
            # Unreadable is not empty. Simulating over somebody's head is the
            # one failure here that actually matters.
            return False
        return state.state.casefold() in _AWAY_STATES

    def _allowed(self) -> bool:
        """Whether the house-wide rules let a simulation run at all."""
        rules = self.runtime.hub.simulation_rules
        if not rules:
            return True
        controller = next(iter(self.runtime.controllers.values()), None)
        if controller is None:
            return True
        verdict = controller.rules_allow(rules)
        if not verdict:
            _LOGGER.debug("Presence simulation blocked by %s", verdict.blocked_by)
        return bool(verdict)

    # -- the away helper ---------------------------------------------------

    @callback
    def _handle_away_change(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in UNREADABLE:
            return
        if self._house_is_empty():
            self.hass.async_create_task(self.async_start(reason="house empty"))
        else:
            self.hass.async_create_task(self.async_stop(reason="somebody home"))

    # -- running -----------------------------------------------------------

    async def async_start(self, *, reason: str = "", forced: bool = False) -> bool:
        """Begin simulating. Returns whether anything actually started."""
        if self._running:
            return False
        if not forced and not self._allowed():
            return False
        self._forced = forced
        self._running = True
        _LOGGER.info("Presence simulation started (%s)", reason or "asked to")
        self.hass.bus.async_fire(EVENT_SIMULATION, {"state": "started", "why": reason})

        for room_id, controller in self.runtime.controllers.items():
            if self._may_simulate(controller):
                await self._async_begin_room(room_id, controller)

        self._notify()
        return True

    async def async_stop(self, *, reason: str = "") -> None:
        """Stop, and put back every room this turned on."""
        if not self._running:
            return
        self._running = False
        self._forced = False
        _LOGGER.info("Presence simulation stopped (%s)", reason or "asked to")

        for room_id, run in list(self._runs.items()):
            if run.cancel is not None:
                run.cancel()
            controller = self.runtime.controllers.get(room_id)
            if controller is None or not run.lit:
                continue
            if controller.held_by_hand:
                # Somebody came home and reached for a switch on the way in.
                continue
            await controller.async_set_mode(RoomMode.OFF)
        self._runs.clear()
        self.hass.bus.async_fire(EVENT_SIMULATION, {"state": "stopped", "why": reason})
        self._notify()

    def _may_simulate(self, controller) -> bool:
        """Whether this room takes part right now.

        The house's rules are not enough on their own: "after dark" is the
        same everywhere, but a room may add its own, and a room behind a
        closed blind proves nothing to anybody outside.
        """
        room = controller.room
        if not room.simulate or room.simulation_mode is SimulationMode.NONE:
            return False
        if not (verdict := controller.rules_allow(room.simulation_rules)):
            _LOGGER.debug(
                "%s sits out the simulation: %s", room.name, verdict.blocked_by
            )
            return False
        if room.simulate_only_when_covers_open and not self._covers_open(room):
            _LOGGER.debug("%s sits out the simulation: blinds are not open", room.name)
            return False
        return True

    def _covers_open(self, room) -> bool:
        """Every one of this room's covers, open.

        A cover nobody can read counts as not open. Failing to simulate is the
        safe direction: the cost is one dark room, where the other way round
        is lighting a room nobody can see and believing it did something.
        """
        covers = room.presence_covers
        if not covers:
            # Nothing to be behind. The room is as visible as it ever is.
            return True
        for entity_id in covers:
            state = self.hass.states.get(entity_id)
            if state is None or state.state.casefold() != "open":
                return False
        return True

    async def _async_begin_room(self, room_id: str, controller) -> None:
        room = controller.room
        run = _RoomRun()
        self._runs[room_id] = run

        if room.simulation_mode is SimulationMode.ADAPTIVE:
            await controller.async_set_adaptive()
            run.lit = True
            return
        if room.simulation_mode is SimulationMode.SCENE:
            if room.simulation_scene_id in controller.scenes:
                await controller.async_set_mode(
                    RoomMode.SCENE, room.simulation_scene_id
                )
                run.lit = True
            else:
                _LOGGER.warning(
                    "%s simulates a scene it no longer has; sitting this one out",
                    room.name,
                )
            return

        steps = await self._async_timeline(controller)
        if not steps:
            # Nothing recorded to replay. Lighting the room all evening
            # instead would be a worse impression of somebody being in than
            # leaving it dark.
            _LOGGER.info("%s has no history to replay; leaving it as it is", room.name)
            return
        run.steps = steps
        run.started = dt_util.utcnow()
        self._schedule(room_id)

    async def _async_timeline(self, controller) -> tuple[ReplayStep, ...]:
        """What this room did on the same weekday, moved about a bit."""
        light = self.runtime.room_lights.get(controller.room.subentry_id)
        entity_id = getattr(light, "entity_id", None)
        if not entity_id:
            return ()

        hub = self.runtime.hub
        now = dt_util.utcnow()
        start, end = window_for(now, hub.simulation_days_back)
        recorded = await self._async_history(entity_id, start, end)
        if not recorded:
            return ()
        return build_timeline(
            recorded,
            start,
            jitter=hub.simulation_jitter_minutes * 60,
            rng=random.Random(),
        )

    async def _async_history(
        self, entity_id: str, start: datetime.datetime, end: datetime.datetime
    ) -> list[RecordedState]:
        """A day of one room, from the recorder's own executor.

        Guarded rather than required: a house with no recorder should lose
        replay and keep everything else, so this returns nothing rather than
        raising.
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import (
                get_significant_states,
            )
        except ImportError:  # pragma: no cover - recorder ships with core
            return []

        try:
            states = await get_instance(self.hass).async_add_executor_job(
                lambda: get_significant_states(
                    self.hass,
                    start,
                    end,
                    [entity_id],
                    include_start_time_state=True,
                    significant_changes_only=False,
                )
            )
        except Exception:
            _LOGGER.warning(
                "Could not read history for %s; it will not be replayed", entity_id
            )
            return []

        # Which rooms were lit when, and nothing else. Recorded brightness is
        # deliberately dropped: the room's own curve knows what nine in the
        # evening should look like, and replaying last week's exact level
        # would fight it for no gain anybody outside could see.
        return [
            RecordedState(when=state.last_changed, on=state.state == "on")
            for state in (states or {}).get(entity_id, [])
            if state.state not in UNREADABLE
        ]

    # -- the timeline ------------------------------------------------------

    @callback
    def _schedule(self, room_id: str) -> None:
        run = self._runs.get(room_id)
        if run is None or run.at >= len(run.steps):
            return
        elapsed = (dt_util.utcnow() - run.started).total_seconds()
        wait = max(0.0, run.steps[run.at].after - elapsed)

        @callback
        def _fire(_now) -> None:
            run.cancel = None
            self.hass.async_create_task(self._async_apply(room_id))

        run.cancel = async_call_later(self.hass, wait, _fire)

    async def _async_apply(self, room_id: str) -> None:
        run = self._runs.get(room_id)
        controller = self.runtime.controllers.get(room_id)
        if run is None or controller is None or not self._running:
            return
        step = run.steps[run.at]
        run.at += 1

        if controller.held_by_hand:
            # Somebody is in after all, or was when they left. Either way the
            # room is theirs; the replay carries on scheduling but stops
            # touching it.
            self._schedule(room_id)
            return

        if step.on:
            await controller.async_set_adaptive()
            run.lit = True
        else:
            await controller.async_set_mode(RoomMode.OFF)
            run.lit = False
        self._schedule(room_id)
