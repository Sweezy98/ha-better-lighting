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


class TestEverySettingIsReachable:
    """The panel edits the same tables the config flow renders."""

    async def test_the_schema_describes_every_form(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        _entry, client = await _setup(hass, hass_ws_client)

        await client.send_json({"id": 1, "type": f"{DOMAIN}/schema"})
        result = (await client.receive_json())["result"]

        assert set(result["forms"]) == {
            "hub",
            "zone",
            "mode",
            "switch",
            "calibration",
            "scene",
            "preset",
            "rule",
        }
        # Every field the config flow has, the panel can draw.
        described = {
            field["key"]
            for form in result["forms"].values()
            for group in form
            for field in group["fields"]
        }
        from custom_components.better_lighting.const import ZONE_SPECS

        assert {spec.key for spec in ZONE_SPECS} <= described

    async def test_labels_follow_the_requested_language(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        _entry, client = await _setup(hass, hass_ws_client)

        await client.send_json({"id": 1, "type": f"{DOMAIN}/schema", "language": "de"})
        result = (await client.receive_json())["result"]

        assert result["labels"]["data"]["night_behavior"] == "Was der Nachtmodus tut"
        assert result["labels"]["options"]["night_behavior"]["scene"].startswith("Eine")

    async def test_the_config_carries_rooms_modes_and_the_hub(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        _entry, client = await _setup(hass, hass_ws_client)

        await client.send_json({"id": 1, "type": f"{DOMAIN}/config"})
        result = (await client.receive_json())["result"]

        assert "hub" in result and "modes" in result
        assert result["rooms"][0]["data"]["lights"] == ["light.one"]

    async def test_saving_a_room_keeps_its_scenes(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        """Settings and collections are edited separately; one must not eat
        the other."""
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]
        before = entry.subentries[zone_id].data["scenes"]

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_zone",
                "zone_id": zone_id,
                "data": {"name": "Kitchen", "lights": ["light.one"], "interval": 120},
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        assert entry.subentries[zone_id].data["scenes"] == before
        assert entry.subentries[zone_id].data["interval"] == 120

    async def test_a_room_claiming_another_rooms_light_is_refused(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        _entry, client = await _setup(hass, hass_ws_client)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_zone",
                "data": {"name": "Hall", "lights": ["light.one"]},
            }
        )
        result = await client.receive_json()

        assert not result["success"]
        assert "light_in_other_zone" in result["error"]["message"]

    async def test_the_hub_saves(self, hass: HomeAssistant, hass_ws_client) -> None:
        entry, client = await _setup(hass, hass_ws_client)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_hub",
                "options": {"min_brightness_pct": 7},
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        assert entry.options["min_brightness_pct"] == 7

    async def test_a_mode_can_be_added_and_removed(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_mode",
                "data": {"name": "Cinema", "states": ["playing"], "rules": []},
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        mode_id = next(
            sub.subentry_id
            for sub in entry.subentries.values()
            if sub.title == "Cinema"
        )
        await client.send_json(
            {"id": 2, "type": f"{DOMAIN}/delete_mode", "mode_id": mode_id}
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        assert mode_id not in entry.subentries

    async def test_a_mode_with_no_states_is_refused(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        _entry, client = await _setup(hass, hass_ws_client)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_mode",
                "data": {"name": "Empty", "states": []},
            }
        )
        result = await client.receive_json()

        assert not result["success"]
        assert "no_states" in result["error"]["message"]

    async def test_a_rooms_switches_can_be_saved_as_a_list(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_zone_collection",
                "zone_id": zone_id,
                "key": "switches",
                "items": [
                    {"name": "Door", "binding_type": "service_only"},
                    {"name": "Oven", "binding_type": "service_only"},
                ],
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        stored = entry.subentries[zone_id].data["switches"]
        assert [s["name"] for s in stored] == ["Door", "Oven"]
        # Each gets an id so a later edit can find it again.
        assert all(s["switch_id"] for s in stored)

    async def test_saving_a_scene_list_keeps_its_per_light_entries(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        """The form describes a scene's name; its lights are not form fields."""
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]
        scenes = [dict(s) for s in entry.subentries[zone_id].data["scenes"]]
        scenes[0]["name"] = "Renamed"

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_zone_collection",
                "zone_id": zone_id,
                "key": "scenes",
                "items": scenes,
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        stored = entry.subentries[zone_id].data["scenes"][0]
        assert stored["name"] == "Renamed"
        assert stored["lights"], "per-light entries were dropped"

    async def test_a_light_calibration_list_saves(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_zone_collection",
                "zone_id": zone_id,
                "key": "light_profiles",
                "items": [{"light_entity": "light.one", "brightness_offset_pct": -12}],
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        stored = entry.subentries[zone_id].data["light_profiles"]
        assert stored[0]["brightness_offset_pct"] == -12

    async def test_a_hub_with_nothing_in_it_still_answers(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        """A fresh install opens the panel before it has any rooms."""
        await async_setup_component(hass, "websocket_api", {})
        await setup_members(hass, [MemberLight("One")])
        entry = hub_entry(subentries_data=[])
        await setup_hub(hass, entry)
        client = await hass_ws_client(hass)

        await client.send_json({"id": 1, "type": f"{DOMAIN}/config"})
        result = (await client.receive_json())["result"]

        assert result == {"rooms": [], "modes": [], "hub": {}}

    async def test_a_room_can_be_created_from_the_panel(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        await async_setup_component(hass, "websocket_api", {})
        await setup_members(hass, [MemberLight("One")])
        entry = hub_entry(subentries_data=[])
        await setup_hub(hass, entry)
        client = await hass_ws_client(hass)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_zone",
                "data": {"name": "Hall", "lights": ["light.one"]},
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        assert hass.states.get("light.hall") is not None


class TestThePanelReplacesTheFlows:
    """Everything the config flows can do, the panel can do."""

    async def test_colour_presets_can_be_managed(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_hub",
                "options": {
                    "color_presets": [
                        {
                            "name": "TV orange",
                            "color_format": "rgb_color",
                            "rgb_color": [255, 140, 40],
                        }
                    ]
                },
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        assert entry.options["color_presets"][0]["name"] == "TV orange"

    async def test_a_modes_rules_can_be_managed(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_mode",
                "data": {
                    "name": "Cinema",
                    "states": ["playing"],
                    "rules": [
                        {
                            "mode_states": ["playing"],
                            "zones": zone_id,
                            "action": "turn_off",
                        }
                    ],
                },
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        mode = next(sub for sub in entry.subentries.values() if sub.title == "Cinema")
        assert mode.data["rules"][0]["action"] == "turn_off"
        # And the mode is live, not merely stored.
        assert hass.states.get("select.cinema_state") is not None

    async def test_a_switchs_cycle_order_can_be_set(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        entry, client = await _setup(hass, hass_ws_client)
        zone_id = subentry_ids(entry)["Kitchen"]
        scene_id = entry.subentries[zone_id].data["scenes"][0]["scene_id"]

        await client.send_json(
            {
                "id": 1,
                "type": f"{DOMAIN}/save_zone_collection",
                "zone_id": zone_id,
                "key": "switches",
                "items": [
                    {
                        "name": "Door",
                        "binding_type": "service_only",
                        "scene_order": [scene_id],
                    }
                ],
            }
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()

        stored = entry.subentries[zone_id].data["switches"][0]
        assert stored["scene_order"] == [scene_id]

    async def test_the_rule_form_asks_for_its_choices_at_runtime(
        self, hass: HomeAssistant, hass_ws_client
    ) -> None:
        """A rule's states come from its mode, its scenes from its room."""
        _entry, client = await _setup(hass, hass_ws_client)

        await client.send_json({"id": 1, "type": f"{DOMAIN}/schema"})
        rule = (await client.receive_json())["result"]["forms"]["rule"]
        fields = {f["key"]: f for group in rule for f in group["fields"]}

        assert fields["mode_states"]["options_key"] == "mode_states"
        assert fields["zones"]["options_key"] == "zones"
        assert fields["scene_id"]["options_key"] == "scenes"


class TestThePanelScriptIsNotCachedForever:
    """An upgrade must not keep serving the previous page."""

    @pytest.mark.skipif(
        not importlib.util.find_spec("hass_frontend"),
        reason="no frontend installed",
    )
    async def test_the_module_url_carries_the_scripts_fingerprint(
        self, hass: HomeAssistant
    ) -> None:
        from custom_components.better_lighting.panel import _fingerprint

        await setup_members(hass, [MemberLight("One")])
        await setup_hub(hass, hub_entry())

        panel = hass.data["frontend_panels"][DOMAIN]
        module_url = panel.config["_panel_custom"]["module_url"]
        assert module_url.endswith(f"?v={_fingerprint()}")

    @pytest.mark.skipif(
        not importlib.util.find_spec("hass_frontend"),
        reason="no frontend installed",
    )
    async def test_a_changed_script_gets_a_different_url(
        self, hass: HomeAssistant, monkeypatch
    ) -> None:
        """The whole point: same URL after an upgrade means the old page."""
        from custom_components.better_lighting import panel

        await setup_members(hass, [MemberLight("One")])
        await setup_hub(hass, hub_entry())
        before = hass.data["frontend_panels"][DOMAIN].config["_panel_custom"][
            "module_url"
        ]

        monkeypatch.setattr(panel, "_fingerprint", lambda: "deadbeefcafe")
        await panel.async_setup_panel(hass)
        after = hass.data["frontend_panels"][DOMAIN].config["_panel_custom"][
            "module_url"
        ]

        assert after != before
        assert after.endswith("?v=deadbeefcafe")

    @pytest.mark.skipif(
        not importlib.util.find_spec("hass_frontend"),
        reason="no frontend installed",
    )
    async def test_unloading_takes_the_page_out_of_the_sidebar(
        self, hass: HomeAssistant
    ) -> None:
        """Uninstalling otherwise left a sidebar item loading a dead script."""
        await setup_members(hass, [MemberLight("One")])
        entry = await setup_hub(hass, hub_entry())
        assert DOMAIN in hass.data["frontend_panels"]

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert DOMAIN not in hass.data["frontend_panels"]

    @pytest.mark.skipif(
        not importlib.util.find_spec("hass_frontend"),
        reason="no frontend installed",
    )
    async def test_it_comes_back_on_a_reload(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One")])
        entry = await setup_hub(hass, hub_entry())

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert DOMAIN in hass.data["frontend_panels"]
