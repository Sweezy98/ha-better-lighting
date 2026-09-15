"""Is anybody in this room?

Only the *input* here. What presence does on its own -- lighting a room on
entry, the cover gate, the ignore flags -- is milestone 5. A cross-zone mode
needs just two facts: whether the room is occupied now, and a notification when
it has been empty long enough to count.

The clear delay matters more than it looks. Occupancy sensors flicker, and a
mode that acted on every flicker would switch a room off while somebody was
still reaching for the biscuit tin.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.const import STATE_HOME, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

if TYPE_CHECKING:
    from .models import ZoneConfig

_LOGGER = logging.getLogger(__name__)

_OCCUPIED_STATES = (STATE_ON, STATE_HOME)


class ZonePresence:
    """Tracks one room's occupancy sensor, if it has one."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone: ZoneConfig,
        *,
        on_occupied: Callable[[], None] | None = None,
        on_cleared: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self.zone = zone
        self._on_occupied = on_occupied
        self._on_cleared = on_cleared

        self._occupied: bool | None = None
        self._clear_timer: CALLBACK_TYPE | None = None
        self._unsubscribers: list[CALLBACK_TYPE] = []

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        entity_id = self.zone.presence_entity
        if not entity_id:
            return
        self._occupied = self._read(entity_id)
        self._unsubscribers.append(
            async_track_state_change_event(self.hass, [entity_id], self._handle_change)
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
        return bool(self.zone.presence_entity)

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

    # -- tracking ----------------------------------------------------------

    def _read(self, entity_id: str) -> bool | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        return state.state in _OCCUPIED_STATES

    @callback
    def _handle_change(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            # An unavailable sensor tells us nothing. Holding the previous
            # answer is safer than inventing a new one.
            return

        occupied = new_state.state in _OCCUPIED_STATES
        if occupied == self._occupied:
            return
        self._occupied = occupied

        if occupied:
            self._cancel_timer()
            _LOGGER.debug("%s: occupied", self.zone.name)
            if self._on_occupied is not None:
                self._on_occupied()
            return

        self._arm_clear_timer()

    @callback
    def _arm_clear_timer(self) -> None:
        self._cancel_timer()
        delay = self.zone.presence_clear_delay

        @callback
        def _cleared(_now) -> None:
            self._clear_timer = None
            _LOGGER.debug("%s: clear", self.zone.name)
            if self._on_cleared is not None:
                self._on_cleared()

        if not delay:
            _cleared(None)
            return
        self._clear_timer = async_call_later(self.hass, delay, _cleared)

    @callback
    def _cancel_timer(self) -> None:
        if self._clear_timer is not None:
            self._clear_timer()
            self._clear_timer = None
