"""A window is open, so stop attracting everything outside.

Requirement 4. The sensor itself is the easy part; the delays are what make it
usable. Without a close delay a slamming window flickers the room, and without
an open delay a door you walk through triggers the amber scene for the two
seconds it takes to shut it again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.const import (
    STATE_CLOSED,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
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

if TYPE_CHECKING:
    from .models import ZoneConfig

_LOGGER = logging.getLogger(__name__)

# Window sensors are binary_sensors (on = open) or covers (open = open), and
# both vocabularies turn up in the same house.
_OPEN_STATES = (STATE_ON, STATE_OPEN)
_CLOSED_STATES = (STATE_OFF, STATE_CLOSED)


class WindowWatcher:
    """Tracks a room's door and window sensors."""

    def __init__(
        self,
        hass: HomeAssistant,
        zone: ZoneConfig,
        *,
        on_open: Callable[[], None] | None = None,
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self.zone = zone
        self._on_open = on_open
        self._on_closed = on_closed

        self._open: bool = False
        self._timer: CALLBACK_TYPE | None = None
        self._unsubscribers: list[CALLBACK_TYPE] = []

    # -- lifecycle ---------------------------------------------------------

    async def async_setup(self) -> None:
        if not self.zone.window_entities:
            return
        self._open = self._any_open()
        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass, list(self.zone.window_entities), self._handle_change
            )
        )

    @callback
    def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._cancel_timer()

    # -- state -------------------------------------------------------------

    @property
    def watching(self) -> bool:
        return bool(self.zone.window_entities)

    @property
    def is_open(self) -> bool:
        return self._open

    def _any_open(self) -> bool:
        for entity_id in self.zone.window_entities:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                # An unreadable sensor is not evidence of an open window.
                continue
            if state.state in _OPEN_STATES:
                return True
        return False

    # -- tracking ----------------------------------------------------------

    @callback
    def _handle_change(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        if new_state.state not in (*_OPEN_STATES, *_CLOSED_STATES):
            return

        now_open = self._any_open()
        if now_open == self._open:
            return

        delay = (
            self.zone.insect_open_delay if now_open else self.zone.insect_close_delay
        )
        self._arm(now_open, delay)

    @callback
    def _arm(self, now_open: bool, delay: int) -> None:
        self._cancel_timer()

        @callback
        def _settle(_now) -> None:
            self._timer = None
            # Re-read rather than trusting the value captured when the timer
            # was armed: the window may have been closed and reopened while it
            # ran, and only the current answer matters.
            actual = self._any_open()
            if actual == self._open:
                return
            self._open = actual
            _LOGGER.debug(
                "%s: window %s", self.zone.name, "open" if actual else "closed"
            )
            callback_fn = self._on_open if actual else self._on_closed
            if callback_fn is not None:
                callback_fn()

        if not delay:
            _settle(None)
            return
        self._timer = async_call_later(self.hass, delay, _settle)

    @callback
    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None
