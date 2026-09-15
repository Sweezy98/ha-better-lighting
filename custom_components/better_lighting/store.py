"""Persisting what a restart must not lose.

Almost all of this integration's state is safely rebuilt from live entity
states after a restart. One thing is not: a cross-zone mode's snapshot. It
records what the rooms looked like *before* the film started, and there is no
way to recover that once the film is playing -- re-deriving it from live state
would capture the film-watching state and the eventual restore would relight
nothing.

So the session is written to disk. Manual overrides deliberately are not: they
express "leave this alone for now", and the most defensible reading of a
restart is that "for now" has ended.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .session import ModeSnapshot

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.sessions"

# Writes are debounced: a mode moving between playing and paused a few times
# does not need a disk write each way.
SAVE_DELAY = 5


@dataclass(slots=True)
class PersistedSession:
    """One mode's session, as it survives a restart."""

    mode_id: str
    session_id: str
    state: str
    opted_out: list[str] = field(default_factory=list)
    snapshot: ModeSnapshot | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode_id": self.mode_id,
            "session_id": self.session_id,
            "state": self.state,
            "opted_out": list(self.opted_out),
            "snapshot": self.snapshot.as_dict() if self.snapshot else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PersistedSession:
        snapshot = raw.get("snapshot")
        return cls(
            mode_id=raw["mode_id"],
            session_id=raw["session_id"],
            state=raw["state"],
            opted_out=list(raw.get("opted_out") or ()),
            snapshot=ModeSnapshot.from_dict(snapshot) if snapshot else None,
        )


class SessionStore:
    """Reads and writes the session file."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._sessions: dict[str, PersistedSession] = {}

    async def async_load(self) -> None:
        try:
            raw = await self._store.async_load()
        except Exception:
            _LOGGER.exception("Could not read stored sessions; starting clean")
            raw = None
        if not raw:
            return
        for mode_id, session in (raw.get("sessions") or {}).items():
            try:
                self._sessions[mode_id] = PersistedSession.from_dict(session)
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning(
                    "Discarding an unreadable stored session for %s", mode_id
                )

    def get(self, mode_id: str) -> PersistedSession | None:
        return self._sessions.get(mode_id)

    def put(self, session: PersistedSession) -> None:
        self._sessions[session.mode_id] = session
        self._schedule_save()

    def drop(self, mode_id: str) -> None:
        if self._sessions.pop(mode_id, None) is not None:
            self._schedule_save()

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)

    def _as_dict(self) -> dict[str, Any]:
        return {
            "sessions": {
                mode_id: session.as_dict()
                for mode_id, session in self._sessions.items()
            }
        }

    async def async_flush(self) -> None:
        """Write immediately.

        Called when the entry unloads, so a reload -- which is a restart in
        miniature -- does not lose a session to the debounce window.
        """
        await self._store.async_save(self._as_dict())
