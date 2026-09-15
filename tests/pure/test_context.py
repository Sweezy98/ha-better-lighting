"""The context registry: telling our own commands from a user's."""

from __future__ import annotations

import time

from homeassistant.core import Context

from custom_components.better_lighting.context import (
    ECHO_WINDOW,
    ContextRegistry,
)


class TestOwnership:
    def test_recognises_a_context_it_minted(self):
        registry = ContextRegistry()
        context = registry.new_context("zone1", "tick")
        assert registry.is_ours(context)

    def test_recognises_a_child_context(self):
        """Home Assistant nests contexts, so a member's change is often a child."""
        registry = ContextRegistry()
        ours = registry.new_context("zone1", "tick")
        child = Context(parent_id=ours.id)
        assert registry.is_ours(child)

    def test_rejects_an_unrelated_context(self):
        registry = ContextRegistry()
        registry.new_context("zone1", "tick")
        assert not registry.is_ours(Context())

    def test_rejects_none(self):
        assert not ContextRegistry().is_ours(None)

    def test_records_why(self):
        registry = ContextRegistry()
        context = registry.new_context("kitchen", "activate")
        origin = registry.origin(context)
        assert origin is not None
        assert origin.zone_id == "kitchen"
        assert origin.reason == "activate"


class TestEchoWindow:
    def test_a_fresh_change_to_a_commanded_light_is_an_echo(self):
        """Template lights and bridges break the context chain entirely."""
        registry = ContextRegistry()
        registry.note_command("light.one")
        # A brand new context, as a hub's fan-out would produce.
        assert registry.is_echo("light.one", Context())

    def test_an_uncommanded_light_is_not_an_echo(self):
        registry = ContextRegistry()
        registry.note_command("light.one")
        assert not registry.is_echo("light.two", Context())

    def test_the_window_expires(self, monkeypatch):
        registry = ContextRegistry()
        registry.note_command("light.one")
        later = time.monotonic() + ECHO_WINDOW + 1
        monkeypatch.setattr(time, "monotonic", lambda: later)
        assert not registry.is_echo("light.one", Context())

    def test_our_own_context_is_an_echo_regardless_of_timing(self, monkeypatch):
        registry = ContextRegistry()
        context = registry.new_context("zone1", "tick")
        later = time.monotonic() + ECHO_WINDOW + 100
        monkeypatch.setattr(time, "monotonic", lambda: later)
        assert registry.is_echo("light.one", context)


class TestPruning:
    def test_drops_entries_past_the_ttl(self, monkeypatch):
        registry = ContextRegistry(ttl=10)
        registry.new_context("zone1", "tick")
        assert len(registry) == 1

        later = time.monotonic() + 100
        monkeypatch.setattr(time, "monotonic", lambda: later)
        registry.new_context("zone1", "tick")
        # The stale entry is evicted when the next one is registered.
        assert len(registry) == 1

    def test_respects_the_size_cap(self):
        registry = ContextRegistry(max_entries=10)
        for _ in range(50):
            registry.new_context("zone1", "tick")
        assert len(registry) <= 11

    def test_clear(self):
        registry = ContextRegistry()
        registry.new_context("zone1", "tick")
        registry.note_command("light.one")
        registry.clear()
        assert len(registry) == 0
        assert not registry.is_echo("light.one", Context())
