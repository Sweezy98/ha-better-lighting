"""Recognising our own service calls.

Every command Better Lighting issues carries a ``Context`` registered here, so
listeners can tell an echo of our own output from a genuine external change.
Getting this wrong is the classic failure of both light-group and adaptive
integrations: the group commands a member, hears the resulting state change,
mistakes it for a user twiddling the bulb, and either loops or permanently
disables itself.

Unlike Adaptive Lighting we do **not** pack a marker into the 26-character
context id. That trick exists because it has no shared memory to consult; we
have a per-entry runtime object, so Home Assistant can generate an ordinary
ULID and we simply remember which ones were ours. That sidesteps the question
of whether a hand-built id still satisfies the recorder.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

from homeassistant.core import Context

# How long a context stays interesting. An in-flight command is resolved in
# seconds; anything older cannot still be an echo.
DEFAULT_TTL = 300.0
DEFAULT_MAX_ENTRIES = 4096

# A member state change this soon after we commanded that entity is treated as
# our echo even when the context does not match. Template lights, Zigbee group
# commands and hub fan-out all break the context chain, and without this window
# their echoes look exactly like a user reaching for the switch.
ECHO_WINDOW = 0.6


@dataclass(frozen=True, slots=True)
class ContextOrigin:
    """Why we issued a command, for diagnostics and log tracing."""

    room_id: str
    reason: str
    created_at: float


class ContextRegistry:
    """Remembers the contexts we issued, with a TTL so it cannot grow forever."""

    def __init__(
        self, ttl: float = DEFAULT_TTL, max_entries: int = DEFAULT_MAX_ENTRIES
    ) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._contexts: OrderedDict[str, ContextOrigin] = OrderedDict()
        # entity_id -> monotonic timestamp of our last command to it.
        self._last_command: dict[str, float] = {}

    def new_context(
        self, room_id: str, reason: str, parent: Context | None = None
    ) -> Context:
        """Mint a context for an outgoing command and remember it."""
        context = Context(parent_id=parent.id if parent else None)
        self.register(context, room_id, reason)
        return context

    def register(self, context: Context, room_id: str, reason: str) -> None:
        now = time.monotonic()
        self._contexts[context.id] = ContextOrigin(room_id, reason, now)
        self._contexts.move_to_end(context.id)
        self._prune(now)

    def note_command(self, entity_id: str) -> None:
        """Record that we have just commanded ``entity_id``, for the echo window."""
        self._last_command[entity_id] = time.monotonic()

    def is_ours(self, context: Context | None) -> bool:
        """Did we cause this?

        Checks the parent link too: Home Assistant nests contexts when one call
        leads to another, so a member's state change often carries a child of
        the context we minted rather than the context itself.
        """
        if context is None:
            return False
        return context.id in self._contexts or (
            context.parent_id is not None and context.parent_id in self._contexts
        )

    def is_echo(self, entity_id: str, context: Context | None = None) -> bool:
        """True if this change is almost certainly the result of our own command."""
        if self.is_ours(context):
            return True
        last = self._last_command.get(entity_id)
        return last is not None and (time.monotonic() - last) <= ECHO_WINDOW

    def origin(self, context: Context | None) -> ContextOrigin | None:
        if context is None:
            return None
        return self._contexts.get(context.id) or (
            self._contexts.get(context.parent_id) if context.parent_id else None
        )

    def clear(self) -> None:
        self._contexts.clear()
        self._last_command.clear()

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl
        while self._contexts:
            oldest = next(iter(self._contexts.values()))
            if oldest.created_at >= cutoff and len(self._contexts) <= self._max_entries:
                break
            self._contexts.popitem(last=False)
        if len(self._last_command) > self._max_entries:
            self._last_command.clear()

    def __len__(self) -> int:
        return len(self._contexts)
