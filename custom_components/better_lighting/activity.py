"""The activity log: what happened, and kept for a while.

Every interesting thing this integration does already goes on the event bus,
which is how the panel watches it live. The bus, though, remembers nothing --
so a switch that misbehaved at three in the morning leaves no trace by
breakfast, which is exactly when somebody comes looking.

So the same events are written here as well, to this integration's own store
rather than to the recorder: they are ours, they are small, and putting them
in the recorder would mean a schema and a migration for something that is
read by one page. Swept by age on every write and at startup, with the age
set in the global settings; zero keeps nothing at all.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORE_KEY = f"{DOMAIN}.activity"
STORE_VERSION = 1

# A ceiling as well as an age, so a loop that fires every second cannot fill
# the disk before the sweep next runs. Generous, because every adaptive step
# of every room is in here too.
MAX_ENTRIES = 5000

# Everything the integration decides, as one event: the bus events kept for
# automations say what changed, this says what was weighed and why.
EVENT_ACTIVITY = f"{DOMAIN}_activity"

# What set the current chain of work going -- a press, presence, a film, the
# interval. Carried with the task rather than passed down every call, so a
# mode change three calls deep still knows it was somebody at the door.
CAUSE: ContextVar[str] = ContextVar(f"{DOMAIN}_cause", default="")


@contextlib.contextmanager
def caused_by(cause: str) -> Iterator[None]:
    """Name what started the work done inside this block.

    Set only where work actually starts -- a press, a sensor, the interval --
    so the innermost of those is the honest answer. A timer booked during a
    press would otherwise still claim to be that press an hour later.
    """
    token = CAUSE.set(cause)
    try:
        yield
    finally:
        CAUSE.reset(token)


@callback
def async_note(
    hass: HomeAssistant,
    what: str,
    *,
    room_id: str | None = None,
    room: str | None = None,
    **detail: Any,
) -> None:
    """Put one decision on the bus, and so in the log."""
    hass.bus.async_fire(
        EVENT_ACTIVITY,
        {
            "what": what,
            "room_id": room_id,
            "room": room,
            "cause": CAUSE.get() or None,
            **{key: value for key, value in detail.items() if value is not None},
        },
    )


# How often the store is actually written. Every entry would mean a disk write
# per press; this batches them, and nothing here is worth a lost second of.
SAVE_DELAY = 30


@dataclass(frozen=True, slots=True)
class Entry:
    """One thing that happened."""

    at: str
    kind: str
    data: dict[str, Any]


class ActivityLog:
    """The last while of what the integration did, across restarts."""

    def __init__(self, hass: HomeAssistant, retention_hours: int) -> None:
        self.hass = hass
        self.retention_hours = retention_hours
        self._store: Store = Store(hass, STORE_VERSION, STORE_KEY)
        self._entries: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        self._entries = list((stored or {}).get("entries") or ())
        # A restart is the right moment to drop what has aged out, since the
        # sweep otherwise only happens when something new arrives.
        if self._prune():
            self._schedule_save()

    @callback
    def async_record(self, event: Event) -> None:
        """Note one of our own bus events."""
        if not self.retention_hours:
            return
        self._entries.append(
            asdict(
                Entry(
                    at=dt_util.utcnow().isoformat(),
                    kind=str(event.event_type).removeprefix(f"{DOMAIN}_"),
                    data=dict(event.data),
                )
            )
        )
        self._prune()
        self._schedule_save()

    def recent(self, limit: int = 500) -> list[dict[str, Any]]:
        """The newest entries first, which is the order they are read in."""
        return list(reversed(self._entries[-limit:]))

    @callback
    def async_clear(self) -> None:
        self._entries = []
        self._schedule_save()

    def _prune(self) -> bool:
        """Drop what has aged out. True when anything went."""
        before = len(self._entries)
        if self.retention_hours:
            cutoff = dt_util.utcnow() - dt_util.dt.timedelta(hours=self.retention_hours)
            self._entries = [
                entry
                for entry in self._entries
                if (moment := dt_util.parse_datetime(entry.get("at") or "")) is not None
                and moment >= cutoff
            ]
        else:
            self._entries = []
        del self._entries[:-MAX_ENTRIES]
        return len(self._entries) != before

    def _schedule_save(self) -> None:
        self._store.async_delay_save(lambda: {"entries": self._entries}, SAVE_DELAY)

    async def async_remove(self) -> None:
        """Take the store with the integration."""
        await self._store.async_remove()
