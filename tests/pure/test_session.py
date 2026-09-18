"""Snapshots and deferred actions. Pure: no hass fixture, no event loop."""

from __future__ import annotations

from custom_components.better_lighting.session import (
    DeferredAction,
    DeferredRegistry,
    LightSnapshotEntry,
    ModeSnapshot,
    RoomAction,
    RoomSnapshot,
    is_still_wanted,
)


def action(room="kitchen", session="s1", state="playing", **kw) -> DeferredAction:
    return DeferredAction(
        room_id=room,
        session_id=session,
        mode_id="cinema",
        mode_state=state,
        action=RoomAction.TURN_OFF,
        **kw,
    )


class TestRegistry:
    def test_one_pending_action_per_room(self):
        """A queue would only create ordering bugs."""
        registry = DeferredRegistry()
        registry.enqueue(action())
        registry.enqueue(action())
        assert len(registry) == 1

    def test_separate_rooms_coexist(self):
        registry = DeferredRegistry()
        registry.enqueue(action("kitchen"))
        registry.enqueue(action("hall"))
        assert len(registry) == 2

    def test_dropping_a_room(self):
        registry = DeferredRegistry()
        registry.enqueue(action("kitchen"))
        registry.enqueue(action("hall"))
        assert len(registry.drop_room("kitchen")) == 1
        assert [a.room_id for a in registry] == ["hall"]

    def test_dropping_a_session(self):
        registry = DeferredRegistry()
        registry.enqueue(action("kitchen", "s1"))
        registry.enqueue(action("hall", "s2"))
        registry.drop_session("s1")
        assert [a.room_id for a in registry] == ["hall"]

    def test_expiry(self):
        """A stuck sensor must not leave an instruction armed forever."""
        registry = DeferredRegistry()
        registry.enqueue(action(created_at=0.0, expires_at=100.0))
        assert registry.drop_expired(50.0) == []
        assert len(registry.drop_expired(150.0)) == 1
        assert len(registry) == 0

    def test_no_expiry_when_unset(self):
        registry = DeferredRegistry()
        registry.enqueue(action(expires_at=None))
        assert registry.drop_expired(1e9) == []


class TestStillWanted:
    """Everything that must cancel a deferred action between queue and fire."""

    def _check(self, **overrides):
        base = {
            "active_session_id": "s1",
            "active_state": "playing",
            "opted_out": set(),
            "room_is_off": False,
            "room_is_manual": False,
            "now": 0.0,
        }
        return is_still_wanted(action(), **{**base, **overrides})

    def test_still_wanted(self):
        assert self._check() == (True, "ok")

    def test_session_ended(self):
        assert self._check(active_session_id=None)[1] == "session_ended"

    def test_a_different_session(self):
        assert self._check(active_session_id="s2")[1] == "session_ended"

    def test_the_mode_moved_on(self):
        """The new state's own rule decides afresh."""
        assert self._check(active_state="paused")[1] == "state_changed"

    def test_the_user_took_the_room_back(self):
        assert self._check(opted_out={"kitchen"})[1] == "opted_out"

    def test_already_satisfied(self):
        assert self._check(room_is_off=True)[1] == "already_satisfied"

    def test_manual_override(self):
        assert self._check(room_is_manual=True)[1] == "manual_override"

    def test_expired(self):
        wanted, reason = is_still_wanted(
            action(expires_at=10.0),
            active_session_id="s1",
            active_state="playing",
            opted_out=set(),
            room_is_off=False,
            room_is_manual=False,
            now=99.0,
        )
        assert (wanted, reason) == (False, "expired")


class TestSnapshots:
    def test_previously_on(self):
        snapshot = RoomSnapshot(
            room_id="kitchen",
            mode="adaptive",
            scene_id=None,
            lights=(
                LightSnapshotEntry("light.one", True, 180),
                LightSnapshotEntry("light.two", False),
            ),
        )
        assert snapshot.previously_on == frozenset({"light.one"})

    def test_round_trips_through_storage(self):
        snapshot = ModeSnapshot(
            session_id="s1",
            mode_id="cinema",
            taken_at="2026-01-01T00:00:00+00:00",
            rooms={
                "kitchen": RoomSnapshot(
                    room_id="kitchen",
                    mode="scene",
                    scene_id="abc",
                    lights=(
                        LightSnapshotEntry(
                            "light.one", True, 180, {"color_temp_kelvin": 2700}
                        ),
                    ),
                )
            },
        )
        restored = ModeSnapshot.from_dict(snapshot.as_dict())
        assert restored.session_id == "s1"
        light = restored.rooms["kitchen"].lights[0]
        assert light.brightness == 180
        assert light.color == {"color_temp_kelvin": 2700}

    def test_opting_out_marks_the_room_not_to_be_restored(self):
        snapshot = RoomSnapshot("kitchen", "adaptive", None, ())
        assert snapshot.restore_on_exit is True
        snapshot.restore_on_exit = False
        assert RoomSnapshot.from_dict(snapshot.as_dict()).restore_on_exit is False
