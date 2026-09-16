"""Scenes as wired into Home Assistant: the subentry flow and the mode select."""

from __future__ import annotations

from typing import ClassVar

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.better_lighting.const import SubentryType
from tests.conftest import (
    MemberLight,
    hub_entry,
    setup_hub,
    setup_members,
    zone_subentry,
)

ZONE = "light.kitchen"
SELECT = "select.kitchen_scenes"


def scene_subentry(
    name: str = "Cosy",
    override: str = "both",
    brightness: int = 20,
    color_format: str = "color_temp_kelvin",
    **overrides,
) -> ConfigSubentryData:
    data = {
        "name": name,
        "icon": "mdi:palette",
        "override_mode": override,
        "brightness_pct": brightness,
        "color_format": color_format,
        "color_temp_kelvin": 2200,
        "transition": 0,
        "on_lights_only": False,
        "ignore_presence": False,
        "others": "adaptive",
        "on_unsupported_color": "adaptive",
        **overrides,
    }
    return ConfigSubentryData(
        data=data,
        subentry_type=SubentryType.SCENE.value,
        title=name,
        unique_id=f"scene:{name.lower()}",
    )


async def _select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": SELECT, "option": option},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, *scenes) -> None:
    await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
    await setup_hub(hass, hub_entry(subentries_data=[zone_subentry(), *scenes]))
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ZONE}, blocking=True
    )
    await hass.async_block_till_done()


class TestModeSelect:
    async def test_lists_adaptive_and_every_scene(self, hass: HomeAssistant):
        """No "off": switching a room off is the light entity's job."""
        await _setup(hass, scene_subentry("Cosy"), scene_subentry("Bright"))
        options = hass.states.get(SELECT).attributes["options"]
        assert options == ["Adaptive", "Cosy", "Bright"]

    async def test_adaptive_is_the_only_option_without_scenes(
        self, hass: HomeAssistant
    ):
        await _setup(hass)
        assert hass.states.get(SELECT).attributes["options"] == ["Adaptive"]

    async def test_starts_in_adaptive(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry())
        assert hass.states.get(SELECT).state == "Adaptive"

    async def test_selecting_a_scene_applies_it(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Cosy", brightness=10))
        before = hass.states.get("light.one").attributes["brightness"]

        await _select(hass, "Cosy")

        assert hass.states.get(SELECT).state == "Cosy"
        after = hass.states.get("light.one").attributes
        assert after["brightness"] < before
        assert after["color_temp_kelvin"] == 2200

    async def test_returning_to_adaptive(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Cosy", brightness=10))
        await _select(hass, "Cosy")
        await _select(hass, "Adaptive")

        assert hass.states.get(SELECT).state == "Adaptive"
        assert hass.states.get("light.one").attributes["color_temp_kelvin"] != 2200

    async def test_a_dark_room_still_reports_a_readable_option(
        self, hass: HomeAssistant
    ):
        """The select answers "what should this look like", not "is it on"."""
        await _setup(hass, scene_subentry())
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": ZONE}, blocking=True
        )
        await hass.async_block_till_done()

        state = hass.states.get(SELECT)
        assert state.state in state.attributes["options"]
        assert state.state == "Adaptive"

    async def test_reports_the_active_scene_id(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Cosy"))
        await _select(hass, "Cosy")
        assert hass.states.get(SELECT).attributes["bl_scene_id"] is not None

    async def test_a_scene_named_off_is_now_just_a_scene(self, hass: HomeAssistant):
        """Nothing to shadow any more, so the name is left alone."""
        await _setup(hass, scene_subentry("Off"))
        assert hass.states.get(SELECT).attributes["options"] == ["Adaptive", "Off"]

    async def test_a_scene_named_adaptive_is_disambiguated(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Adaptive"))
        options = hass.states.get(SELECT).attributes["options"]
        assert options[0] == "Adaptive"
        assert options[1].startswith("Adaptive (")


class TestOverrideModes:
    """Requirement 7, end to end."""

    async def test_brightness_only_keeps_the_adaptive_colour(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Task", override="brightness", brightness=90))
        adaptive_kelvin = hass.states.get("light.one").attributes["color_temp_kelvin"]

        await _select(hass, "Task")

        after = hass.states.get("light.one").attributes
        # Not exactly equal: the curve is always evaluated at the moment the
        # fade will *finish*, and a scene activation fades over a different
        # duration than a turn-on, so the kept axis can drift a few Kelvin.
        # What matters is that it is still the sun's colour rather than the
        # scene's, which is more than a thousand Kelvin away.
        assert after["color_temp_kelvin"] == pytest.approx(adaptive_kelvin, abs=100)
        assert after["brightness"] >= 220

    async def test_colour_only_keeps_the_adaptive_brightness(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Amber", override="color", brightness=5))
        adaptive_brightness = hass.states.get("light.one").attributes["brightness"]

        await _select(hass, "Amber")

        after = hass.states.get("light.one").attributes
        assert after["color_temp_kelvin"] == 2200
        # As above: a few units of drift is the curve being aimed correctly,
        # not the scene taking the axis over.
        assert after["brightness"] == pytest.approx(adaptive_brightness, abs=5)

    async def test_neither_only_selects_lights(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Reading", override="neither"))
        before = dict(hass.states.get("light.one").attributes)

        await _select(hass, "Reading")

        after = hass.states.get("light.one").attributes
        assert after["brightness"] == pytest.approx(before["brightness"], abs=5)
        assert after["color_temp_kelvin"] == pytest.approx(
            before["color_temp_kelvin"], abs=100
        )


class TestOnLightsOnly:
    """Requirement 6."""

    async def test_dark_lights_stay_dark(self, hass: HomeAssistant):
        await setup_members(
            hass,
            [MemberLight("One", is_on=True, brightness=100), MemberLight("Two")],
        )
        await setup_hub(
            hass,
            hub_entry(
                subentries_data=[
                    zone_subentry(),
                    scene_subentry("Dim", brightness=10, on_lights_only=True),
                ]
            ),
        )

        await _select(hass, "Dim")

        assert hass.states.get("light.one").state == "on"
        assert hass.states.get("light.two").state == "off"


def zone_scene(name: str = "Movie", lights=None, **overrides) -> dict:
    """One of a room's own scenes, as stored inside the zone."""
    return {
        "scene_id": f"scene_{name.lower().replace(' ', '_')}",
        "name": name,
        "icon": "mdi:palette",
        "transition": 0,
        "on_lights_only": False,
        "ignore_presence": False,
        "others": "adaptive",
        "on_unsupported_color": "adaptive",
        "lights": lights or {},
        **overrides,
    }


def light_spec(**fields) -> dict:
    spec = {"action": "apply", "color_format": "inherit"}
    spec.update(fields)
    return spec


class TestTheZoneFlowOwnsScenes:
    """Scenes are built inside the room they belong to."""

    ZONE_INPUT: ClassVar = {
        "name": "Living Room",
        "lights": ["light.one"],
        "icon": "mdi:sofa",
        "group": {},
        "adaptive": {},
        "night": {},
        "power": {},
        "presence": {},
        "insect": {},
    }

    async def _open(self, hass: HomeAssistant):
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(
            hass, hub_entry(subentries_data=[zone_subentry("Kitchen", ["light.two"])])
        )
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SubentryType.ZONE.value),
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], self.ZONE_INPUT
        )
        assert result["step_id"] == "menu"
        return entry, result

    async def _menu(self, hass: HomeAssistant, result, step: str):
        return await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": step}
        )

    async def test_a_scene_is_added_under_its_room(self, hass: HomeAssistant) -> None:
        _entry, result = await self._open(hass)

        result = await self._menu(hass, result, "scenes")
        result = await self._menu(hass, result, "add_scene")
        assert result["step_id"] == "add_scene"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {"name": "Reading", "icon": "mdi:book", "transition": 1, "advanced": {}},
        )
        assert result["step_id"] == "scene_lights"

        result = await self._menu(hass, result, "add_light")
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                "light": "light.one",
                "action": "apply",
                "brightness_pct": 40,
                "color_format": "none",
            },
        )
        assert result["step_id"] == "scene_lights"

        result = await self._menu(hass, result, "save_scene")
        result = await self._menu(hass, result, "menu")
        result = await self._menu(hass, result, "finish")
        assert result["type"] is FlowResultType.CREATE_ENTRY

        scenes = result["data"]["scenes"]
        assert [scene["name"] for scene in scenes] == ["Reading"]
        assert scenes[0]["lights"]["light.one"]["brightness_pct"] == 40

    async def test_the_colour_is_asked_for_on_its_own(
        self, hass: HomeAssistant
    ) -> None:
        """One colour format per light, so it cannot carry two."""
        _entry, result = await self._open(hass)
        result = await self._menu(hass, result, "scenes")
        result = await self._menu(hass, result, "add_scene")
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {"name": "Movie", "icon": "mdi:movie", "transition": 1, "advanced": {}},
        )
        result = await self._menu(hass, result, "add_light")
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                "light": "light.one",
                "action": "apply",
                "brightness_pct": 7,
                "color_format": "rgb_color",
            },
        )
        assert result["step_id"] == "light_color"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"rgb_color": [255, 140, 40]}
        )
        assert result["step_id"] == "scene_lights"

        result = await self._menu(hass, result, "save_scene")
        result = await self._menu(hass, result, "menu")
        result = await self._menu(hass, result, "finish")
        spec = result["data"]["scenes"][0]["lights"]["light.one"]
        assert spec["rgb_color"] == [255, 140, 40]
        assert "color_temp_kelvin" not in spec

    async def test_a_scene_can_be_captured_from_the_room(
        self, hass: HomeAssistant
    ) -> None:
        """The way round a config flow having no colour wheel."""
        _entry, result = await self._open(hass)
        hass.states.async_set(
            "light.one",
            "on",
            {"brightness": 128, "color_mode": "color_temp", "color_temp_kelvin": 2700},
        )

        result = await self._menu(hass, result, "scenes")
        result = await self._menu(hass, result, "capture_scene")
        assert result["step_id"] == "capture_scene"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"name": "As it is now"}
        )
        result = await self._menu(hass, result, "menu")
        result = await self._menu(hass, result, "finish")

        spec = result["data"]["scenes"][0]["lights"]["light.one"]
        assert spec["color_temp_kelvin"] == 2700
        assert 49 <= spec["brightness_pct"] <= 51


class TestScenesBelongToOneRoom:
    async def _two_rooms(self, hass: HomeAssistant):
        await setup_members(
            hass,
            [
                MemberLight("Lr", is_on=True, brightness=200),
                MemberLight("Kt", is_on=True, brightness=200),
            ],
        )
        entry = hub_entry(
            subentries_data=[
                zone_subentry(
                    "Living Room",
                    ["light.lr"],
                    scenes=[
                        zone_scene(
                            "Reading",
                            {"*": light_spec(brightness_pct=35, color_format="none")},
                        )
                    ],
                ),
                zone_subentry(
                    "Kitchen",
                    ["light.kt"],
                    scenes=[
                        zone_scene(
                            "Cooking",
                            {"*": light_spec(brightness_pct=90, color_format="none")},
                        )
                    ],
                ),
            ]
        )
        await setup_hub(hass, entry)
        return entry

    async def test_each_room_lists_only_its_own(self, hass: HomeAssistant) -> None:
        await self._two_rooms(hass)

        living = hass.states.get("select.living_room_scenes").attributes["options"]
        kitchen = hass.states.get("select.kitchen_scenes").attributes["options"]

        assert living == ["Adaptive", "Reading"]
        assert kitchen == ["Adaptive", "Cooking"]

    async def test_applying_one_never_touches_the_other_room(
        self, hass: HomeAssistant
    ) -> None:
        await self._two_rooms(hass)
        before = hass.states.get("light.kt").attributes["brightness"]

        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.living_room_scenes", "option": "Reading"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert hass.states.get("light.kt").attributes["brightness"] == before

    async def test_two_rooms_can_both_have_a_reading_scene(
        self, hass: HomeAssistant
    ) -> None:
        """The reason scenes moved: the same word, different lights."""
        await setup_members(
            hass,
            [
                MemberLight("Lr", is_on=True, brightness=200),
                MemberLight("Bd", is_on=True, brightness=200),
            ],
        )
        entry = hub_entry(
            subentries_data=[
                zone_subentry(
                    "Living Room",
                    ["light.lr"],
                    scenes=[
                        zone_scene(
                            "Reading",
                            {"*": light_spec(brightness_pct=35, color_format="none")},
                        )
                    ],
                ),
                zone_subentry(
                    "Bedroom",
                    ["light.bd"],
                    scenes=[
                        zone_scene(
                            "Reading",
                            {"*": light_spec(brightness_pct=80, color_format="none")},
                            scene_id="scene_reading_bedroom",
                        )
                    ],
                ),
            ]
        )
        await setup_hub(hass, entry)

        for room in ("living_room", "bedroom"):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": f"select.{room}_scenes", "option": "Reading"},
                blocking=True,
            )
        await hass.async_block_till_done()

        # Same name, different rooms, different results.
        assert (
            hass.states.get("light.lr").attributes["brightness"]
            < hass.states.get("light.bd").attributes["brightness"]
        )


class TestNightScene:
    async def test_night_can_apply_a_designated_scene(
        self, hass: HomeAssistant
    ) -> None:
        hass.states.async_set("input_boolean.asleep", "off")
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            options={"night_source_entity": "input_boolean.asleep"},
            subentries_data=[
                zone_subentry(
                    scenes=[
                        zone_scene(
                            "Nightlight",
                            {
                                "*": light_spec(
                                    brightness_pct=2,
                                    color_format="color_temp_kelvin",
                                    color_temp_kelvin=2200,
                                )
                            },
                        )
                    ],
                    night_behavior="scene",
                    night_scene_id="scene_nightlight",
                )
            ],
        )
        await setup_hub(hass, entry)

        await hass.services.async_call(
            "light", "turn_on", {"entity_id": ZONE}, blocking=True
        )
        await hass.async_block_till_done()

        hass.states.async_set("input_boolean.asleep", "on")
        await hass.async_block_till_done()

        assert hass.states.get("light.one").attributes["color_temp_kelvin"] == 2200
