"""Is anybody in this room, and may presence act on it?

Two separate questions, deliberately kept apart. Occupancy is a fact about the
room, and a cross-room mode gates on it directly. The **cover gate** is a
policy about whether presence should be *lighting* the room at all: requirement
3 only wants the lights coming on when the blinds are down, because a sunlit
room does not need them.

The clear delay matters more than it looks. Occupancy sensors flicker, and
acting on every flicker would switch a room off while somebody was still
reaching for the biscuit tin.

The gate is watched as well as read. A cover closing while somebody is already
in the room is exactly as much a reason to light it as somebody walking into an
already-dark one -- and only tracking the presence sensor would miss it.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.const import (
    STATE_CLOSED,
    STATE_HOME,
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
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import CoverCondition
from .triggers import UNREADABLE, active_entities, any_active
from .zones import Zone

if TYPE_CHECKING:
    from .models import RoomConfig

_LOGGER = logging.getLogger(__name__)

_OCCUPIED_STATES = (STATE_ON, STATE_HOME)


class RoomPresence:
    """Tracks one room's occupancy sensor, if it has one."""

    def __init__(
        self,
        hass: HomeAssistant,
        room: RoomConfig,
        *,
        on_occupied: Callable[[], None] | None = None,
        on_cleared: Callable[[], None] | None = None,
        on_gate_opened: Callable[[], None] | None = None,
        on_countdown: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self.room = room
        self._on_occupied = on_occupied
        self._on_cleared = on_cleared
        self._on_gate_opened = on_gate_opened
        self._on_countdown = on_countdown

        self._occupied: bool | None = None
        # When the wait ends, or None when nothing is waiting.
        self.clear_at: datetime.datetime | None = None
        self._clear_timer: CALLBACK_TYPE | None = None
        self._unsubscribers: list[CALLBACK_TYPE] = []

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        if covers := self.room.presence_covers:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass, list(covers), self._handle_cover_change
                )
            )

        watched = active_entities(self.room.triggers)
        if not watched:
            return
        self._occupied = self._read_triggers()
        self._unsubscribers.append(
            async_track_state_change_event(self.hass, watched, self._handle_change)
        )

    @callback
    def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._cancel_timer()

    # -- state -------------------------------------------------------------

    @property
    def has_sensor(self) -> bool:
        return bool(active_entities(self.room.triggers))

    def _read_triggers(self) -> bool | None:
        """Whether any trigger is asking, or None if none can be read.

        Held rather than guessed: every sensor being unavailable is not
        evidence that a room is empty.
        """
        states = {
            entity_id: (
                state.state
                if (state := self.hass.states.get(entity_id)) is not None
                else None
            )
            for entity_id in active_entities(self.room.triggers)
        }
        if all(value in UNREADABLE for value in states.values()):
            return None
        return any_active(self.room.triggers, states)

    @property
    def occupied(self) -> bool | None:
        """True, False, or None when this room has no sensor at all."""
        return self._occupied if self.has_sensor else None

    @property
    def is_clear(self) -> bool:
        """Safe to act on the assumption the room is empty.

        A room with no sensor counts as clear: we cannot know better, and the
        alternative would be never acting on any unmonitored room.
        """
        return not self.has_sensor or self._occupied is False

    @property
    def covers_ok(self) -> bool:
        """Whether the covers allow presence to light this room.

        An unknown or unavailable cover blocks by default: the point of the
        gate is "it is dark in here", and a blind we cannot see is not evidence
        of that. Configurable, because a flaky cover would otherwise disable
        the feature entirely.
        """
        condition = self.room.cover_condition
        if condition is CoverCondition.IGNORE or not self.room.presence_covers:
            return True

        closed: list[bool] = []
        for entity_id in self.room.presence_covers:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                if self.room.cover_unknown_blocks:
                    return False
                continue
            closed.append(state.state == STATE_CLOSED)

        if not closed:
            return not self.room.cover_unknown_blocks
        if condition is CoverCondition.ANY_CLOSED:
            return any(closed)
        return all(closed)

    # -- tracking ----------------------------------------------------------

    def _read(self, entity_id: str) -> bool | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        return state.state in _OCCUPIED_STATES

    @callback
    def _handle_change(self, event: Event[EventStateChangedData]) -> None:
        # Asked of the whole set, not of the sensor that moved: with two
        # triggers on one room, one going quiet is not the room going quiet.
        occupied = self._read_triggers()
        if occupied is None or occupied == self._occupied:
            return
        self._occupied = occupied

        if occupied:
            self._cancel_timer()
            _LOGGER.debug("%s: occupied", self.room.name)
            if self._on_occupied is not None:
                self._on_occupied()
            return

        self._arm_clear_timer()

    @callback
    def _handle_cover_change(self, event: Event[EventStateChangedData]) -> None:
        """A blind moved. That can be the moment presence becomes allowed."""
        was_ok = self._covers_ok_before(event)
        now_ok = self.covers_ok
        if now_ok and not was_ok and self._occupied:
            # Somebody is already in the room and the blinds have just come
            # down. Requirement 3's second half.
            _LOGGER.debug("%s: cover gate opened while occupied", self.room.name)
            if self._on_gate_opened is not None:
                self._on_gate_opened()

    def _covers_ok_before(self, event: Event[EventStateChangedData]) -> bool:
        """What the gate said immediately before this change."""
        changed = event.data["entity_id"]
        old_state = event.data.get("old_state")
        condition = self.room.cover_condition
        if condition is CoverCondition.IGNORE or not self.room.presence_covers:
            return True

        closed: list[bool] = []
        for entity_id in self.room.presence_covers:
            state = (
                old_state if entity_id == changed else self.hass.states.get(entity_id)
            )
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                if self.room.cover_unknown_blocks:
                    return False
                continue
            closed.append(state.state == STATE_CLOSED)

        if not closed:
            return not self.room.cover_unknown_blocks
        if condition is CoverCondition.ANY_CLOSED:
            return any(closed)
        return all(closed)

    @callback
    def _arm_clear_timer(self) -> None:
        self._cancel_timer()
        delay = self.room.presence_clear_delay

        @callback
        def _cleared(_now) -> None:
            self._clear_timer = None
            self.clear_at = None
            _LOGGER.debug("%s: clear", self.room.name)
            if self._on_cleared is not None:
                self._on_cleared()

        if not delay:
            _cleared(None)
            return
        # Kept so a dashboard can say how much of the wait is left, rather
        # than only that something is pending.
        self.clear_at = dt_util.utcnow() + datetime.timedelta(seconds=delay)
        self._clear_timer = async_call_later(self.hass, delay, _cleared)
        if self._on_countdown is not None:
            self._on_countdown()

    @callback
    def _cancel_timer(self) -> None:
        was_waiting = self.clear_at is not None
        self.clear_at = None
        if self._clear_timer is not None:
            self._clear_timer()
            self._clear_timer = None
        if was_waiting and self._on_countdown is not None:
            self._on_countdown()


class ZoneOccupancy:
    """Is anybody at this particular part of the room?

    Deliberately much smaller than :class:`RoomPresence`. A zone asks one
    question -- somebody is here, or has not been here for a while -- and the
    cover gate, the on-action and the off-action are all the room's business.
    The clear delay is here for the same reason it is there: somebody standing
    up to fetch a coffee should not put the desk back into the film.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone: Zone,
        *,
        on_occupied: Callable[[], None],
        on_cleared: Callable[[], None],
        on_countdown: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self.zone = zone
        self._on_occupied = on_occupied
        self._on_cleared = on_cleared
        self._on_countdown = on_countdown
        self._occupied: bool | None = None
        # When the wait ends, or None when nothing is waiting.
        self.clear_at: datetime.datetime | None = None
        self._clear_timer: CALLBACK_TYPE | None = None
        self._unsubscribe: CALLBACK_TYPE | None = None

    @callback
    def async_setup(self) -> None:
        watched = active_entities(self.zone.triggers)
        if not watched:
            return
        self._occupied = self._read_triggers()
        self._unsubscribe = async_track_state_change_event(
            self.hass, watched, self._handle_change
        )

    def _read_triggers(self) -> bool | None:
        """Whether any of this zone's triggers is asking.

        None when not one of them can be read, which is held rather than
        guessed: a sensor that has dropped out is not evidence of an empty
        desk.
        """
        states = {
            entity_id: (
                state.state
                if (state := self.hass.states.get(entity_id)) is not None
                else None
            )
            for entity_id in active_entities(self.zone.triggers)
        }
        if all(value in UNREADABLE for value in states.values()):
            return None
        return any_active(self.zone.triggers, states)

    @callback
    def async_shutdown(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._cancel_timer()

    @property
    def occupied(self) -> bool:
        """Whether somebody is here. A zone with no sensor never is."""
        return bool(self.zone.triggers) and self._occupied is True

    @callback
    def _handle_change(self, event: Event[EventStateChangedData]) -> None:
        # Asked of the whole set: with a motion sensor and a door on one zone,
        # the motion stopping is not the zone going quiet.
        occupied = self._read_triggers()
        if occupied is None or occupied == self._occupied:
            return
        self._occupied = occupied

        if occupied:
            self._cancel_timer()
            _LOGGER.debug("%s: occupied", self.zone.name)
            self._on_occupied()
            return
        self._arm_clear_timer()

    @callback
    def _arm_clear_timer(self) -> None:
        self._cancel_timer()

        @callback
        def _cleared(_now) -> None:
            self._clear_timer = None
            self.clear_at = None
            _LOGGER.debug("%s: clear", self.zone.name)
            self._on_cleared()

        delay = self.zone.presence_clear_delay
        if not delay:
            _cleared(None)
            return
        self.clear_at = dt_util.utcnow() + datetime.timedelta(seconds=delay)
        self._clear_timer = async_call_later(self.hass, delay, _cleared)
        if self._on_countdown is not None:
            self._on_countdown()

    @callback
    def _cancel_timer(self) -> None:
        was_waiting = self.clear_at is not None
        self.clear_at = None
        if self._clear_timer is not None:
            self._clear_timer()
            self._clear_timer = None
        if was_waiting and self._on_countdown is not None:
            self._on_countdown()
