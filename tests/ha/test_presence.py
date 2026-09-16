"""Presence, the cover gate, and insect mode: requirements 3 and 4."""

from __future__ import annotations

import datetime as dt

from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from tests.conftest import (
    MemberLight,
    hub_entry,
    setup_hub,
    setup_members,
    zone_subentry,
)
from tests.ha.test_scenes import scene_subentry

ZONE = "light.kitchen"
SELECT = "select.kitchen_scenes"
SENSOR = "binary_sensor.kitchen_presence"
BLIND = "cover.kitchen_blind"
WINDOW = "binary_sensor.kitchen_window"


async def build(hass: HomeAssistant, *, on: bool = False, scenes=(), **zone_kwargs):
    hass.states.async_set(SENSOR, "off")
    await setup_members(
        hass,
        [
            MemberLight("One", is_on=on, brightness=150 if on else None),
            MemberLight("Two"),
        ],
    )
    entry = hub_entry(
        subentries_data=[
            zone_subentry(
                presence_entity=SENSOR,
                presence_clear_delay=0,
                **zone_kwargs,
            ),
            *(scene_subentry(name) for name in scenes),
        ]
    )
    await setup_hub(hass, entry)
    return entry


def scene_id(entry, title: str) -> str:
    return next(
        sub.subentry_id for sub in entry.subentries.values() if sub.title == title
    )


async def occupy(hass: HomeAssistant, occupied: bool = True) -> None:
    hass.states.async_set(SENSOR, "on" if occupied else "off")
    await hass.async_block_till_done()


class TestPresenceTurnsLightsOn:
    """Requirement 3."""

    async def test_walking_in_lights_the_room(self, hass: HomeAssistant) -> None:
        await build(hass)
        assert hass.states.get("light.one").state == "off"

        await occupy(hass)

        assert hass.states.get("light.one").state == "on"
        assert hass.states.get(SELECT).state == "Adaptive"

    async def test_leaving_switches_it_off(self, hass: HomeAssistant) -> None:
        await build(hass)
        await occupy(hass)
        await occupy(hass, False)
        assert hass.states.get("light.one").state == "off"

    async def test_presence_can_be_told_to_leave_the_lights_alone(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, presence_on_action="none")
        await occupy(hass)
        assert hass.states.get("light.one").state == "off"

    async def test_presence_can_apply_a_named_scene(self, hass: HomeAssistant) -> None:
        entry = await build(hass, scenes=("Path",), presence_on_action="scene")
        # Point the room at the scene now that it has an id.
        zone = next(sub for sub in entry.subentries.values() if sub.title == "Kitchen")
        hass.config_entries.async_update_subentry(
            entry,
            zone,
            data={**zone.data, "presence_on_scene_id": scene_id(entry, "Path")},
        )
        await hass.async_block_till_done()

        await occupy(hass)
        assert hass.states.get(SELECT).state == "Path"

    async def test_an_already_lit_room_is_not_restyled(
        self, hass: HomeAssistant
    ) -> None:
        """Walking into a room you already lit should not change it."""
        await build(hass, on=True, scenes=("Cosy",))
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": SELECT, "option": "Cosy"},
            blocking=True,
        )
        await hass.async_block_till_done()

        await occupy(hass)
        assert hass.states.get(SELECT).state == "Cosy"

    async def test_leaving_does_not_undo_a_manual_change(
        self, hass: HomeAssistant
    ) -> None:
        """Switching off behind somebody who set the room by hand is rude."""
        await build(hass)
        await occupy(hass)
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.one", "brightness": 4},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()

        await occupy(hass, False)
        assert hass.states.get("light.one").state == "on"


class TestCoverGate:
    """The second half of requirement 3."""

    async def test_presence_is_blocked_while_a_cover_is_open(
        self, hass: HomeAssistant
    ) -> None:
        hass.states.async_set(BLIND, "open")
        await build(hass, presence_covers=[BLIND])

        await occupy(hass)

        # Daylight is coming in, so the lights stay off.
        assert hass.states.get("light.one").state == "off"

    async def test_presence_works_once_the_cover_is_closed(
        self, hass: HomeAssistant
    ) -> None:
        hass.states.async_set(BLIND, "closed")
        await build(hass, presence_covers=[BLIND])

        await occupy(hass)
        assert hass.states.get("light.one").state == "on"

    async def test_closing_a_cover_while_occupied_lights_the_room(
        self, hass: HomeAssistant
    ) -> None:
        """The case a presence-only listener would miss entirely."""
        hass.states.async_set(BLIND, "open")
        await build(hass, presence_covers=[BLIND])
        await occupy(hass)
        assert hass.states.get("light.one").state == "off"

        hass.states.async_set(BLIND, "closed")
        await hass.async_block_till_done()

        assert hass.states.get("light.one").state == "on"

    async def test_an_unknown_cover_blocks_by_default(
        self, hass: HomeAssistant
    ) -> None:
        hass.states.async_set(BLIND, "unavailable")
        await build(hass, presence_covers=[BLIND])
        await occupy(hass)
        assert hass.states.get("light.one").state == "off"

    async def test_an_unknown_cover_can_be_allowed(self, hass: HomeAssistant) -> None:
        hass.states.async_set(BLIND, "unavailable")
        await build(hass, presence_covers=[BLIND], cover_unknown_blocks=False)
        await occupy(hass)
        assert hass.states.get("light.one").state == "on"

    async def test_the_gate_can_be_ignored(self, hass: HomeAssistant) -> None:
        hass.states.async_set(BLIND, "open")
        await build(hass, presence_covers=[BLIND], cover_condition="ignore")
        await occupy(hass)
        assert hass.states.get("light.one").state == "on"

    async def test_any_closed(self, hass: HomeAssistant) -> None:
        hass.states.async_set(BLIND, "open")
        hass.states.async_set("cover.kitchen_door", "closed")
        await build(
            hass,
            presence_covers=[BLIND, "cover.kitchen_door"],
            cover_condition="any_closed",
        )
        await occupy(hass)
        assert hass.states.get("light.one").state == "on"


class TestNightIgnoresPresence:
    async def test_the_bedroom_case(self, hass: HomeAssistant) -> None:
        """Requirement 3: night mode can silence presence entirely."""
        hass.states.async_set("input_boolean.asleep", "off")
        await build(
            hass,
            night_source_entity="input_boolean.asleep",
            night_ignore_presence=True,
        )
        hass.states.async_set("input_boolean.asleep", "on")
        await hass.async_block_till_done()

        await occupy(hass)

        assert hass.states.get("light.one").state == "off"

    async def test_presence_works_again_once_night_ends(
        self, hass: HomeAssistant
    ) -> None:
        hass.states.async_set("input_boolean.asleep", "on")
        await build(
            hass,
            night_source_entity="input_boolean.asleep",
            night_ignore_presence=True,
        )
        await occupy(hass)
        assert hass.states.get("light.one").state == "off"

        hass.states.async_set("input_boolean.asleep", "off")
        await hass.async_block_till_done()
        await occupy(hass, False)
        await occupy(hass)
        assert hass.states.get("light.one").state == "on"


class TestSceneIgnoresPresence:
    async def test_a_scene_can_silence_presence(self, hass: HomeAssistant) -> None:
        """Requirement 3: the living room during a film."""
        entry = await build(hass, on=True)
        # A scene that says presence has no business here.
        from tests.ha.test_modes import _sub

        hass.config_entries.async_add_subentry(
            entry, _sub(scene_subentry("Movie", brightness=5, ignore_presence=True))
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": SELECT, "option": "Movie"},
            blocking=True,
        )
        await hass.async_block_till_done()

        await occupy(hass)
        await occupy(hass, False)

        # Presence neither restyled it on the way in nor darkened it on the
        # way out.
        assert hass.states.get(SELECT).state == "Movie"
        assert hass.states.get("light.one").state == "on"


class TestInsectMode:
    """Requirement 4."""

    async def _build(self, hass: HomeAssistant, *, on: bool = True, **kwargs):
        hass.states.async_set(WINDOW, "off")
        entry = await build(hass, on=on, scenes=("Amber",), **kwargs)
        zone = next(sub for sub in entry.subentries.values() if sub.title == "Kitchen")
        hass.config_entries.async_update_subentry(
            entry,
            zone,
            data={
                **zone.data,
                "window_entities": [WINDOW],
                "insect_scene_id": scene_id(entry, "Amber"),
                "insect_open_delay": 0,
                "insect_close_delay": 0,
            },
        )
        await hass.async_block_till_done()
        return entry

    async def _open(self, hass: HomeAssistant, is_open: bool = True) -> None:
        hass.states.async_set(WINDOW, "on" if is_open else "off")
        await hass.async_block_till_done()

    async def test_an_open_window_switches_to_the_insect_scene(
        self, hass: HomeAssistant
    ) -> None:
        await self._build(hass)
        await self._open(hass)
        assert hass.states.get(SELECT).attributes["bl_effective_mode"] == "insect"

    async def test_closing_it_puts_the_room_back(self, hass: HomeAssistant) -> None:
        await self._build(hass)
        await self._open(hass)
        await self._open(hass, False)
        assert hass.states.get(SELECT).attributes["bl_effective_mode"] == "adaptive"

    async def test_it_returns_to_the_scene_that_was_showing(
        self, hass: HomeAssistant
    ) -> None:
        entry = await self._build(hass)
        from tests.ha.test_modes import _sub

        hass.config_entries.async_add_subentry(
            entry, _sub(scene_subentry("Cosy", brightness=20))
        )
        await hass.async_block_till_done()
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": SELECT, "option": "Cosy"},
            blocking=True,
        )
        await hass.async_block_till_done()

        await self._open(hass)
        assert hass.states.get(SELECT).attributes["bl_effective_mode"] == "insect"

        await self._open(hass, False)
        assert hass.states.get(SELECT).state == "Cosy"

    async def test_a_dark_room_is_not_lit_by_an_open_window(
        self, hass: HomeAssistant
    ) -> None:
        await self._build(hass, on=False)
        # Nothing was on, so leave it that way.
        await self._open(hass)
        assert hass.states.get("light.one").state == "off"

    async def test_a_press_waves_insect_mode_away(self, hass: HomeAssistant) -> None:
        await self._build(hass)
        await self._open(hass)
        assert hass.states.get(SELECT).attributes["bl_effective_mode"] == "insect"

        await hass.services.async_call(
            "light", "turn_on", {"entity_id": ZONE}, blocking=True
        )
        await hass.async_block_till_done()

        assert hass.states.get(SELECT).attributes["bl_effective_mode"] == "adaptive"

    async def test_a_dismissed_insect_mode_stays_dismissed(
        self, hass: HomeAssistant
    ) -> None:
        """Until the window is closed and opened again."""
        await self._build(hass)
        await self._open(hass)
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": ZONE}, blocking=True
        )
        await hass.async_block_till_done()

        await self._open(hass, False)
        assert hass.states.get(SELECT).attributes["bl_effective_mode"] == "adaptive"

        await self._open(hass)
        assert hass.states.get(SELECT).attributes["bl_effective_mode"] == "insect"

    async def test_the_close_delay_debounces_a_slamming_window(
        self, hass: HomeAssistant, freezer
    ) -> None:
        entry = await self._build(hass)
        zone = next(sub for sub in entry.subentries.values() if sub.title == "Kitchen")
        hass.config_entries.async_update_subentry(
            entry, zone, data={**zone.data, "insect_close_delay": 30}
        )
        await hass.async_block_till_done()

        await self._open(hass)
        await self._open(hass, False)
        # Still amber: the close has not settled yet.
        assert hass.states.get(SELECT).attributes["bl_effective_mode"] == "insect"

        freezer.tick(dt.timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).attributes["bl_effective_mode"] == "adaptive"
