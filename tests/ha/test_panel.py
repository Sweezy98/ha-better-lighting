"""The sidebar panel and the websocket API behind it."""

from __future__ import annotations

import importlib.util

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.better_lighting.const import DOMAIN
from custom_components.better_lighting.panel import DRAFT_ID
from tests.conftest import (
    MemberLight,
    hub_entry,
    setup_hub,
    setup_members,
    subentry_ids,
    zone_subentry,
)
from tests.ha.test_scenes import light_spec, zone_scene

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def _setup(hass: HomeAssistant, ws_client):
    await async_setup_component(hass, "websocket_api", {})
    await setup_members(hass, [MemberLight("One", is_on=True, brightness=200)])
    entry = hub_entry(
        subentries_data=[
            zone_subentry(
                "Kitchen",
                ["light.one"],
                scenes=[
                    zone_scene(
                        "Cosy",
                        {"*": light_spec(brightness_pct=30, color_format="none")},
                    )
                ],
            )
        ]
    )
    await setup_hub(hass, entry)
    return entry, await ws_client(hass)


class TestTheApi:
    async def test_config_lists_rooms_with_their_scenes(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        _entry, client = await _setup(hass, hass_ws_client)

        await client.send_json({"id": 1, "type": f"{DOMAIN}/config"})
        result = await client.receive_json()

        assert result["success"]
        rooms = result["result"]["rooms"]
        assert [room["name"] for room in rooms] == ["Kitchen"]
        assert rooms[0]["lights"] == ["light.one"]
        assert [scene["name"] for scene in rooms[0]["scenes"]] == ["Cosy"]

    async def test_a_draft_reaches_the_bulbs_without_being_saved(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        """The point of the panel: see it before you keep it."""
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/preview",
                "zone_id": zone_id,
                "scene": {
                    "name": "Draft",
                    "lights": {"*": {"action": "apply", "brightness_pct": 5}},
                },
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        assert hass.states.get("light.one").attributes["brightness"] <= 20
        # Nothing was written: a preview is not a save.
        stored = entry.subentries[zone_id].data["scenes"]
        assert [scene["name"] for scene in stored] == ["Cosy"]

    async def test_stopping_a_preview_puts_the_room_back(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]
        controller = entry.runtime_data.controllers[zone_id]

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/preview",
                "zone_id": zone_id,
                "scene": {"lights": {"*": {"action": "apply", "brightness_pct": 5}}},
            }
        )
        await client.receive_json()
        await client.send_json(
            {"id": 2, "type": f"{DOMAIN}/stop_preview", "zone_id": zone_id}
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        assert DRAFT_ID not in controller.scenes

    async def test_saving_adds_the_scene_to_its_room(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_scene",
                "zone_id": zone_id,
                "scene": {
                    "name": "Film",
                    "lights": {"light.one": {"action": "apply", "brightness_pct": 7}},
                },
            }
        )
        result = await client.receive_json()
        await hass.async_block_till_done()

        assert result["success"]
        stored = entry.subentries[zone_id].data["scenes"]
        assert [scene["name"] for scene in stored] == ["Cosy", "Film"]
        # And it is offered in the room straight away.
        assert "Film" in hass.states.get("select.kitchen_scenes").attributes["options"]

    async def test_saving_twice_replaces_rather_than_duplicates(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]
        scene = {"scene_id": "scene_cosy", "name": "Cosy", "lights": {}}

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_scene",
                "zone_id": zone_id,
                "scene": scene,
            }
        )
        await client.receive_json()
        await hass.async_block_till_done()

        stored = entry.subentries[zone_id].data["scenes"]
        assert len(stored) == 1

    async def test_deleting_removes_it(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/delete_scene",
                "zone_id": zone_id,
                "scene_id": "scene_cosy",
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        assert entry.subentries[zone_id].data["scenes"] == []


class TestThePanelItself:
    @pytest.mark.skipif(
        not importlib.util.find_spec("hass_frontend"),
        reason="no frontend installed; the panel is skipped and the rest still works",
    )
    async def test_it_appears_in_the_sidebar(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One")])
        await setup_hub(hass, hub_entry())

        panels = hass.data.get("frontend_panels", {})
        assert DOMAIN in panels
        assert panels[DOMAIN].sidebar_title == "Better Lighting"


async def test_without_a_frontend_everything_else_still_works(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The panel is a nicety. The lighting is not."""
    from custom_components.better_lighting import panel

    async def _no_frontend(*_args, **_kwargs):
        return False

    monkeypatch.setattr(panel, "async_setup_component", _no_frontend)
    await setup_members(hass, [MemberLight("One")])
    entry = await setup_hub(hass, hub_entry())

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("light.kitchen") is not None
