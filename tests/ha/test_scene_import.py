"""Importing Home Assistant's own scenes, splitting them across rooms."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.better_lighting.const import DOMAIN
from tests.conftest import (
    MemberLight,
    hub_entry,
    room_subentry,
    setup_hub,
    setup_members,
    subentry_ids,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def _setup(hass: HomeAssistant, ws_client, scene_entities):
    """Two rooms, one unassigned light, and a Home Assistant scene."""
    await async_setup_component(hass, "websocket_api", {})
    await setup_members(
        hass,
        [
            MemberLight("Lounge", is_on=True, brightness=200),
            MemberLight("Kitchen", is_on=True, brightness=200),
            MemberLight("Shed", is_on=True, brightness=200),
        ],
    )
    assert await async_setup_component(
        hass,
        "scene",
        {
            "scene": [
                {
                    "name": "Movie",
                    "id": "movie",
                    "entities": scene_entities,
                }
            ]
        },
    )
    await hass.async_block_till_done()

    entry = hub_entry(
        subentries_data=[
            room_subentry("Lounge", ["light.lounge"]),
            room_subentry("Kitchen", ["light.kitchen"]),
        ]
    )
    await setup_hub(hass, entry)
    return entry, await ws_client(hass)


class TestSeeingWhatCanBeImported:
    async def test_a_scene_is_reported_split_by_room(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(
            hass,
            hass_ws_client,
            {
                "light.lounge": {"state": "on", "brightness": 25},
                "light.kitchen": {"state": "on", "brightness": 180},
            },
        )
        ids = subentry_ids(entry)

        await client.send_json({"id": 1, "type": f"{DOMAIN}/importable_scenes"})
        result = (await client.receive_json())["result"]

        scene = next(s for s in result["scenes"] if s["name"] == "Movie")
        assert scene["rooms"] == {
            ids["Lounge"]: ["light.lounge"],
            ids["Kitchen"]: ["light.kitchen"],
        }
        assert scene["skipped"] == []

    async def test_lights_in_no_room_are_named_rather_than_dropped(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        """The user should be told what will not come across."""
        _entry, client = await _setup(
            hass,
            hass_ws_client,
            {
                "light.lounge": {"state": "on", "brightness": 25},
                "light.shed": {"state": "on", "brightness": 90},
            },
        )

        await client.send_json({"id": 1, "type": f"{DOMAIN}/importable_scenes"})
        scene = next(
            s
            for s in (await client.receive_json())["result"]["scenes"]
            if s["name"] == "Movie"
        )

        assert scene["skipped"] == ["light.shed"]

    async def test_things_that_are_not_lights_are_skipped(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        hass.states.async_set("switch.fan", "off")
        _entry, client = await _setup(
            hass,
            hass_ws_client,
            {"light.lounge": {"state": "on"}, "switch.fan": "on"},
        )

        await client.send_json({"id": 1, "type": f"{DOMAIN}/importable_scenes"})
        scene = next(
            s
            for s in (await client.receive_json())["result"]["scenes"]
            if s["name"] == "Movie"
        )

        assert scene["skipped"] == ["switch.fan"]


class TestImporting:
    async def test_it_splits_into_one_scene_per_room(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        """A scene belongs to one room here, so a shared one becomes several."""
        entry, client = await _setup(
            hass,
            hass_ws_client,
            {
                "light.lounge": {"state": "on", "brightness": 25},
                "light.kitchen": {"state": "on", "brightness": 180},
            },
        )
        ids = subentry_ids(entry)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/import_scene",
                "entity_id": "scene.movie",
                "room_ids": [ids["Lounge"], ids["Kitchen"]],
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        for room, light, brightness in (
            ("Lounge", "light.lounge", 25),
            ("Kitchen", "light.kitchen", 180),
        ):
            scenes = entry.subentries[ids[room]].data["scenes"]
            assert [s["name"] for s in scenes] == ["Movie"]
            spec = scenes[0]["lights"][light]
            assert spec["brightness_pct"] == pytest.approx(brightness / 255 * 100, 0.1)
            # Only this room's lights came across.
            assert set(scenes[0]["lights"]) == {light}

    async def test_only_the_rooms_asked_for_get_one(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(
            hass,
            hass_ws_client,
            {
                "light.lounge": {"state": "on", "brightness": 25},
                "light.kitchen": {"state": "on", "brightness": 180},
            },
        )
        ids = subentry_ids(entry)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/import_scene",
                "entity_id": "scene.movie",
                "room_ids": [ids["Lounge"]],
            }
        )
        await client.receive_json()
        await hass.async_block_till_done()

        assert entry.subentries[ids["Lounge"]].data["scenes"]
        assert not entry.subentries[ids["Kitchen"]].data.get("scenes")

    async def test_a_light_the_scene_turns_off_comes_across_as_off(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(
            hass, hass_ws_client, {"light.lounge": {"state": "off"}}
        )
        ids = subentry_ids(entry)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/import_scene",
                "entity_id": "scene.movie",
                "room_ids": [ids["Lounge"]],
            }
        )
        await client.receive_json()
        await hass.async_block_till_done()

        spec = entry.subentries[ids["Lounge"]].data["scenes"][0]["lights"][
            "light.lounge"
        ]
        assert spec["action"] == "off"

    async def test_a_colour_temperature_survives_the_trip(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(
            hass,
            hass_ws_client,
            {
                "light.lounge": {
                    "state": "on",
                    "brightness": 120,
                    "color_mode": "color_temp",
                    "color_temp_kelvin": 2700,
                }
            },
        )
        ids = subentry_ids(entry)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/import_scene",
                "entity_id": "scene.movie",
                "room_ids": [ids["Lounge"]],
            }
        )
        await client.receive_json()
        await hass.async_block_till_done()

        spec = entry.subentries[ids["Lounge"]].data["scenes"][0]["lights"][
            "light.lounge"
        ]
        assert spec["color_format"] == "color_temp_kelvin"
        assert spec["color_temp_kelvin"] == 2700

    async def test_the_imported_scene_is_immediately_usable(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(
            hass,
            hass_ws_client,
            {"light.lounge": {"state": "on", "brightness": 25}},
        )
        ids = subentry_ids(entry)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/import_scene",
                "entity_id": "scene.movie",
                "room_ids": [ids["Lounge"]],
            }
        )
        await client.receive_json()
        await hass.async_block_till_done()

        assert "Movie" in hass.states.get("select.lounge_scenes").attributes["options"]
