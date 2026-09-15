"""Scenes as wired into Home Assistant: the subentry flow and the mode select."""

from __future__ import annotations

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
SELECT = "select.kitchen_mode"


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
    async def test_lists_off_adaptive_and_every_scene(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Cosy"), scene_subentry("Bright"))
        options = hass.states.get(SELECT).attributes["options"]
        assert options == ["Off", "Adaptive", "Cosy", "Bright"]

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

    async def test_off_switches_the_room_off(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry())
        await _select(hass, "Off")
        assert hass.states.get("light.one").state == "off"
        assert hass.states.get("light.two").state == "off"

    async def test_reports_the_active_scene_id(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Cosy"))
        await _select(hass, "Cosy")
        assert hass.states.get(SELECT).attributes["bl_scene_id"] is not None

    async def test_a_scene_named_off_does_not_shadow_the_real_option(
        self, hass: HomeAssistant
    ):
        await _setup(hass, scene_subentry("Off"))
        options = hass.states.get(SELECT).attributes["options"]
        assert options[0] == "Off"
        assert len([o for o in options if o.startswith("Off")]) == 2


class TestOverrideModes:
    """Requirement 7, end to end."""

    async def test_brightness_only_keeps_the_adaptive_colour(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Task", override="brightness", brightness=90))
        adaptive_kelvin = hass.states.get("light.one").attributes["color_temp_kelvin"]

        await _select(hass, "Task")

        after = hass.states.get("light.one").attributes
        assert after["color_temp_kelvin"] == adaptive_kelvin
        assert after["brightness"] >= 220

    async def test_colour_only_keeps_the_adaptive_brightness(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Amber", override="color", brightness=5))
        adaptive_brightness = hass.states.get("light.one").attributes["brightness"]

        await _select(hass, "Amber")

        after = hass.states.get("light.one").attributes
        assert after["color_temp_kelvin"] == 2200
        assert after["brightness"] == adaptive_brightness

    async def test_neither_only_selects_lights(self, hass: HomeAssistant):
        await _setup(hass, scene_subentry("Reading", override="neither"))
        before = dict(hass.states.get("light.one").attributes)

        await _select(hass, "Reading")

        after = hass.states.get("light.one").attributes
        assert after["brightness"] == before["brightness"]
        assert after["color_temp_kelvin"] == before["color_temp_kelvin"]


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


class TestSceneFlow:
    async def test_two_step_flow_collects_the_colour(self, hass: HomeAssistant):
        entry = await setup_hub(hass, hub_entry())

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SubentryType.SCENE.value),
            context={"source": config_entries.SOURCE_USER},
        )
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                "name": "Movie",
                "icon": "mdi:movie",
                "override_mode": "both",
                "brightness_pct": 15,
                "color_format": "color_temp_kelvin",
                "transition": 2,
                "advanced": {},
            },
        )
        # Step two asks only for the format that was chosen.
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "color"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"color_temp_kelvin": 2000}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Movie"
        assert result["data"]["color_temp_kelvin"] == 2000

    async def test_brightness_only_scene_skips_the_colour_step(
        self, hass: HomeAssistant
    ):
        entry = await setup_hub(hass, hub_entry())

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SubentryType.SCENE.value),
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                "name": "Task",
                "icon": "mdi:desk-lamp",
                "override_mode": "brightness",
                "brightness_pct": 100,
                "color_format": "color_temp_kelvin",
                "transition": 1,
                "advanced": {},
            },
        )
        # Nothing to ask: the scene does not own the colour axis.
        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_no_colour_format_skips_the_colour_step(self, hass: HomeAssistant):
        entry = await setup_hub(hass, hub_entry())

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SubentryType.SCENE.value),
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                "name": "Plain",
                "icon": "mdi:palette",
                "override_mode": "both",
                "brightness_pct": 50,
                "color_format": "none",
                "transition": 1,
                "advanced": {},
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY


class TestNightScene:
    async def test_night_can_apply_a_designated_scene(self, hass: HomeAssistant):
        hass.states.async_set("input_boolean.asleep", "off")
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        night = scene_subentry(
            "Nightlight", brightness=2, color_format="color_temp_kelvin"
        )
        entry = hub_entry(subentries_data=[zone_subentry(), night])
        # Point the zone at the scene by its generated subentry id.
        await setup_hub(hass, entry)

        scene_id = next(
            sub.subentry_id
            for sub in entry.subentries.values()
            if sub.subentry_type == SubentryType.SCENE.value
        )
        zone = next(
            sub
            for sub in entry.subentries.values()
            if sub.subentry_type == SubentryType.ZONE.value
        )
        hass.config_entries.async_update_subentry(
            entry,
            zone,
            data={
                **zone.data,
                "night_source_entity": "input_boolean.asleep",
                "night_behavior": "scene",
                "night_scene_id": scene_id,
            },
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            "light", "turn_on", {"entity_id": ZONE}, blocking=True
        )
        await hass.async_block_till_done()

        hass.states.async_set("input_boolean.asleep", "on")
        await hass.async_block_till_done()

        assert hass.states.get("light.one").attributes["color_temp_kelvin"] == 2200
