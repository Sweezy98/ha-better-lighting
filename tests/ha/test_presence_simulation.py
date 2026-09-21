"""Presence simulation, end to end.

The pure tests in ``tests/pure/test_simulation.py`` cover turning a recorded
day into things to do. These cover the part that matters to somebody on
holiday: it runs when the house is empty, it stops the instant anybody is
back, and it never touches a room that was told to sit it out.
"""

from __future__ import annotations

import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.better_lighting.simulation import RecordedState
from tests.conftest import (
    MemberLight,
    hub_entry,
    room_subentry,
    setup_hub,
    setup_members,
)

AWAY = "binary_sensor.nobody_home"
LUX = "sensor.outside_lux"
HOLIDAY = "input_boolean.holiday"

DARK_ENOUGH = {
    "name": "After dark",
    "check": "below",
    "condition_entity": LUX,
    "threshold": 10,
}
NOT_ON_HOLIDAY = {
    "name": "Not on holiday",
    "check": "state_is",
    "condition_entity": HOLIDAY,
    "required_state": "off",
}


async def _build(hass: HomeAssistant, *, away: str | None = AWAY, rooms=None, **hub):
    await setup_members(
        hass,
        [
            MemberLight("Lounge Main", is_on=False),
            MemberLight("Bathroom Main", is_on=False),
        ],
    )
    hass.states.async_set(AWAY, "off")

    entry = hub_entry(
        options={"away_entity": away, **hub},
        subentries_data=rooms
        or [
            room_subentry("Lounge", ["light.lounge_main"], simulation_mode="adaptive"),
            room_subentry(
                "Bathroom", ["light.bathroom_main"], simulation_mode="adaptive"
            ),
        ],
    )
    await setup_hub(hass, entry)
    return entry


def _lit(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    return state is not None and state.state == "on"


async def _set(hass: HomeAssistant, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done()


async def _advance(hass: HomeAssistant, freezer, *, seconds: int) -> None:
    """Let time pass. One step is scheduled at a time, so each needs its own
    moment -- which is also how it happens on a real clock."""
    freezer.tick(datetime.timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


class TestWhenItRuns:
    async def test_an_empty_house_starts_it(self, hass: HomeAssistant) -> None:
        entry = await _build(hass)

        await _set(hass, AWAY, "on")

        assert entry.runtime_data.simulation.running
        assert _lit(hass, "light.lounge_main")

    async def test_somebody_home_stops_it(self, hass: HomeAssistant) -> None:
        entry = await _build(hass)
        await _set(hass, AWAY, "on")
        assert _lit(hass, "light.lounge_main")

        await _set(hass, AWAY, "off")

        assert not entry.runtime_data.simulation.running
        assert not _lit(hass, "light.lounge_main")

    async def test_no_away_helper_means_nothing_happens(
        self, hass: HomeAssistant
    ) -> None:
        """Inert until the house says what "empty" means."""
        entry = await _build(hass, away=None)

        await _set(hass, AWAY, "on")

        assert not entry.runtime_data.simulation.running
        assert not _lit(hass, "light.lounge_main")

    async def test_an_unreadable_helper_is_not_an_empty_house(
        self, hass: HomeAssistant
    ) -> None:
        """The one failure that matters is simulating over somebody's head."""
        entry = await _build(hass)

        await _set(hass, AWAY, "unavailable")

        assert not entry.runtime_data.simulation.running


class TestTheRules:
    async def test_a_rule_can_hold_it_back(self, hass: HomeAssistant) -> None:
        entry = await _build(hass, simulation_rules=[DARK_ENOUGH])
        await _set(hass, LUX, "400")

        await _set(hass, AWAY, "on")

        assert not entry.runtime_data.simulation.running
        assert not _lit(hass, "light.lounge_main")

    async def test_and_let_it_through(self, hass: HomeAssistant) -> None:
        entry = await _build(hass, simulation_rules=[DARK_ENOUGH])
        await _set(hass, LUX, "4")

        await _set(hass, AWAY, "on")

        assert entry.runtime_data.simulation.running

    async def test_the_switch_overrides_a_rule(self, hass: HomeAssistant) -> None:
        """So somebody can check their setup without waiting for nightfall."""
        entry = await _build(hass, simulation_rules=[DARK_ENOUGH])
        await _set(hass, LUX, "400")

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.better_lighting_presence_simulation"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert entry.runtime_data.simulation.running


class TestWhichRoomsTakePart:
    async def test_a_room_can_sit_it_out(self, hass: HomeAssistant) -> None:
        """A bathroom nobody can see from the street proves nothing."""
        entry = await _build(
            hass,
            rooms=[
                room_subentry(
                    "Lounge", ["light.lounge_main"], simulation_mode="adaptive"
                ),
                room_subentry("Bathroom", ["light.bathroom_main"], simulate=False),
            ],
        )

        await _set(hass, AWAY, "on")

        assert _lit(hass, "light.lounge_main")
        assert not _lit(hass, "light.bathroom_main")
        assert entry.runtime_data.simulation.rooms_running == frozenset(
            [
                room_id
                for room_id, controller in entry.runtime_data.controllers.items()
                if controller.room.name == "Lounge"
            ]
        )

    async def test_a_room_can_show_a_scene(self, hass: HomeAssistant) -> None:
        entry = await _build(
            hass,
            rooms=[
                room_subentry(
                    "Lounge",
                    ["light.lounge_main"],
                    simulation_mode="scene",
                    simulation_scene_id="evening",
                    scenes=[
                        {
                            "scene_id": "evening",
                            "name": "Evening",
                            "lights": {"*": {"action": "apply", "brightness_pct": 30}},
                        }
                    ],
                )
            ],
        )

        await _set(hass, AWAY, "on")

        controller = next(iter(entry.runtime_data.controllers.values()))
        assert controller.active_scene_id == "evening"

    async def test_a_scene_that_no_longer_exists_sits_the_room_out(
        self, hass: HomeAssistant
    ) -> None:
        """Lenient, like every other dangling reference here."""
        entry = await _build(
            hass,
            rooms=[
                room_subentry(
                    "Lounge",
                    ["light.lounge_main"],
                    simulation_mode="scene",
                    simulation_scene_id="gone",
                )
            ],
        )

        await _set(hass, AWAY, "on")

        assert entry.runtime_data.simulation.running
        assert not _lit(hass, "light.lounge_main")


class TestTheSwitch:
    async def test_it_reports_what_is_running(self, hass: HomeAssistant) -> None:
        await _build(hass)

        await _set(hass, AWAY, "on")

        state = hass.states.get("switch.better_lighting_presence_simulation")
        assert state.state == "on"
        assert len(state.attributes["rooms"]) == 2

    async def test_turning_it_off_puts_the_rooms_back(
        self, hass: HomeAssistant
    ) -> None:
        await _build(hass)
        await _set(hass, AWAY, "on")

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.better_lighting_presence_simulation"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert not _lit(hass, "light.lounge_main")


class TestReplay:
    """The convincing mode: last week, shifted.

    The recorder is stubbed rather than populated. What is being checked is
    that a recorded day becomes scheduled work and that the work runs -- the
    turning of history into steps is covered without a Home Assistant at all
    in ``tests/pure/test_simulation.py``.
    """

    async def _recorded(self, hass: HomeAssistant, monkeypatch, entries):
        from custom_components.better_lighting.simulate import SimulationRunner

        async def _fake(self, entity_id, start, end):
            return [
                RecordedState(when=start + datetime.timedelta(minutes=m), on=on)
                for m, on in entries
            ]

        monkeypatch.setattr(SimulationRunner, "_async_history", _fake)

    async def test_a_recorded_day_becomes_scheduled_work(
        self, hass: HomeAssistant, monkeypatch
    ) -> None:
        await self._recorded(hass, monkeypatch, [(0, False), (30, True), (90, False)])
        entry = await _build(
            hass,
            rooms=[
                room_subentry("Lounge", ["light.lounge_main"], simulation_mode="replay")
            ],
            simulation_jitter_minutes=0,
        )

        await _set(hass, AWAY, "on")

        runner = entry.runtime_data.simulation
        room_id = next(iter(entry.runtime_data.controllers))
        assert [step.on for step in runner._runs[room_id].steps] == [False, True, False]

    async def test_it_lights_the_room_when_the_step_comes_round(
        self, hass: HomeAssistant, monkeypatch, freezer
    ) -> None:
        await self._recorded(hass, monkeypatch, [(0, False), (5, True)])
        await _build(
            hass,
            rooms=[
                room_subentry("Lounge", ["light.lounge_main"], simulation_mode="replay")
            ],
            simulation_jitter_minutes=0,
        )

        await _set(hass, AWAY, "on")

        # The first step is the room as it was when the window opened: off.
        await _advance(hass, freezer, seconds=1)
        assert not _lit(hass, "light.lounge_main")

        await _advance(hass, freezer, seconds=6 * 60)

        assert _lit(hass, "light.lounge_main")

    async def test_a_room_with_no_history_is_left_dark(
        self, hass: HomeAssistant, monkeypatch
    ) -> None:
        """Lighting it all evening instead would be a worse impression of
        somebody being in than leaving it alone."""
        await self._recorded(hass, monkeypatch, [])
        entry = await _build(
            hass,
            rooms=[
                room_subentry("Lounge", ["light.lounge_main"], simulation_mode="replay")
            ],
        )

        await _set(hass, AWAY, "on")

        assert entry.runtime_data.simulation.running
        assert not _lit(hass, "light.lounge_main")


class TestRoomLevelRules:
    """A room's rules are added to the house's, not instead of them."""

    async def test_a_room_rule_can_hold_one_room_back(
        self, hass: HomeAssistant
    ) -> None:
        entry = await _build(
            hass,
            rooms=[
                room_subentry(
                    "Lounge", ["light.lounge_main"], simulation_mode="adaptive"
                ),
                room_subentry(
                    "Bathroom",
                    ["light.bathroom_main"],
                    simulation_mode="adaptive",
                    simulation_rules=[DARK_ENOUGH],
                ),
            ],
        )
        await _set(hass, LUX, "400")

        await _set(hass, AWAY, "on")

        assert entry.runtime_data.simulation.running
        assert _lit(hass, "light.lounge_main")
        assert not _lit(hass, "light.bathroom_main")

    async def test_both_sets_have_to_hold(self, hass: HomeAssistant) -> None:
        """The house says when, the room says whether."""
        await _build(
            hass,
            simulation_rules=[NOT_ON_HOLIDAY],
            rooms=[
                room_subentry(
                    "Lounge",
                    ["light.lounge_main"],
                    simulation_mode="adaptive",
                    simulation_rules=[DARK_ENOUGH],
                )
            ],
        )
        await _set(hass, HOLIDAY, "off")
        await _set(hass, LUX, "4")

        await _set(hass, AWAY, "on")

        assert _lit(hass, "light.lounge_main")

    async def test_a_room_with_no_rules_follows_the_house(
        self, hass: HomeAssistant
    ) -> None:
        await _build(hass)

        await _set(hass, AWAY, "on")

        assert _lit(hass, "light.lounge_main")


class TestTheBlinds:
    """A lit room behind a closed blind convinces nobody outside."""

    async def _with_cover(self, hass: HomeAssistant, **extra):
        return await _build(
            hass,
            rooms=[
                room_subentry(
                    "Lounge",
                    ["light.lounge_main"],
                    simulation_mode="adaptive",
                    presence_covers=["cover.lounge"],
                    simulate_only_when_covers_open=True,
                    **extra,
                )
            ],
        )

    async def test_open_blinds_let_it_run(self, hass: HomeAssistant) -> None:
        await self._with_cover(hass)
        await _set(hass, "cover.lounge", "open")

        await _set(hass, AWAY, "on")

        assert _lit(hass, "light.lounge_main")

    async def test_closed_blinds_sit_the_room_out(self, hass: HomeAssistant) -> None:
        await self._with_cover(hass)
        await _set(hass, "cover.lounge", "closed")

        await _set(hass, AWAY, "on")

        assert not _lit(hass, "light.lounge_main")

    async def test_a_cover_nobody_can_read_counts_as_shut(
        self, hass: HomeAssistant
    ) -> None:
        """Failing to simulate costs one dark room. The other way round is
        lighting a room nobody can see and believing it did something."""
        await self._with_cover(hass)
        await _set(hass, "cover.lounge", "unavailable")

        await _set(hass, AWAY, "on")

        assert not _lit(hass, "light.lounge_main")

    async def test_a_room_with_no_covers_is_as_visible_as_it_ever_is(
        self, hass: HomeAssistant
    ) -> None:
        await _build(
            hass,
            rooms=[
                room_subentry(
                    "Lounge",
                    ["light.lounge_main"],
                    simulation_mode="adaptive",
                    simulate_only_when_covers_open=True,
                )
            ],
        )

        await _set(hass, AWAY, "on")

        assert _lit(hass, "light.lounge_main")

    async def test_the_toggle_off_ignores_the_blinds(self, hass: HomeAssistant) -> None:
        await _build(
            hass,
            rooms=[
                room_subentry(
                    "Lounge",
                    ["light.lounge_main"],
                    simulation_mode="adaptive",
                    presence_covers=["cover.lounge"],
                )
            ],
        )
        await _set(hass, "cover.lounge", "closed")

        await _set(hass, AWAY, "on")

        assert _lit(hass, "light.lounge_main")
