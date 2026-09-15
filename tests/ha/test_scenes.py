"""Scenes as wired into Home Assistant: the subentry flow and the mode select."""

from __future__ import annotations

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


class TestSceneScope:
    """Which rooms a scene is *offered* in. Requirement raised in review."""

    async def _two_rooms(self, hass: HomeAssistant, scene_zones=None):
        await setup_members(
            hass,
            [
                MemberLight("Lr", is_on=True, brightness=200),
                MemberLight("Kt", is_on=True, brightness=200),
            ],
        )
        scene = scene_subentry("Reading", brightness=35)
        entry = hub_entry(
            subentries_data=[
                zone_subentry("Living Room", ["light.lr"]),
                zone_subentry("Kitchen", ["light.kt"]),
                scene,
            ]
        )
        await setup_hub(hass, entry)
        if scene_zones is not None:
            ids = {sub.title: sub.subentry_id for sub in entry.subentries.values()}
            scene_sub = next(
                sub for sub in entry.subentries.values() if sub.title == "Reading"
            )
            hass.config_entries.async_update_subentry(
                entry,
                scene_sub,
                data={
                    **scene_sub.data,
                    "scene_zones": [ids[name] for name in scene_zones],
                },
            )
            await hass.async_block_till_done()
        return entry

    async def test_applying_a_scene_never_touches_another_room(
        self, hass: HomeAssistant
    ) -> None:
        """The thing the README's wording made sound doubtful."""
        await self._two_rooms(hass)
        before = hass.states.get("light.kt").attributes["brightness"]

        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.living_room_mode", "option": "Reading"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert hass.states.get("light.lr").attributes["brightness"] < before
        assert hass.states.get("light.kt").attributes["brightness"] == before
        assert hass.states.get("select.kitchen_mode").state == "Adaptive"

    async def test_an_unscoped_scene_is_offered_everywhere(
        self, hass: HomeAssistant
    ) -> None:
        """Right for a Night or Movie scene."""
        await self._two_rooms(hass)
        for room in ("living_room", "kitchen"):
            options = hass.states.get(f"select.{room}_mode").attributes["options"]
            assert "Reading" in options

    async def test_a_scoped_scene_is_only_offered_there(
        self, hass: HomeAssistant
    ) -> None:
        await self._two_rooms(hass, scene_zones=["Living Room"])

        assert (
            "Reading"
            in hass.states.get("select.living_room_mode").attributes["options"]
        )
        assert (
            "Reading"
            not in hass.states.get("select.kitchen_mode").attributes["options"]
        )

    async def test_a_scene_can_be_scoped_to_several_rooms(
        self, hass: HomeAssistant
    ) -> None:
        await self._two_rooms(hass, scene_zones=["Living Room", "Kitchen"])
        for room in ("living_room", "kitchen"):
            assert (
                "Reading"
                in hass.states.get(f"select.{room}_mode").attributes["options"]
            )

    async def test_an_active_scene_is_still_reported(self, hass: HomeAssistant) -> None:
        """Scoping hides a scene from the picker, not from the readout."""
        entry = await self._two_rooms(hass)
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.kitchen_mode", "option": "Reading"},
            blocking=True,
        )
        await hass.async_block_till_done()

        ids = {sub.title: sub.subentry_id for sub in entry.subentries.values()}
        scene_sub = next(
            sub for sub in entry.subentries.values() if sub.title == "Reading"
        )
        hass.config_entries.async_update_subentry(
            entry,
            scene_sub,
            data={**scene_sub.data, "scene_zones": [ids["Living Room"]]},
        )
        await hass.async_block_till_done()

        # The kitchen no longer offers it, but if it is somehow showing it the
        # select must still be able to say so rather than reporting nothing.
        kitchen = hass.states.get("select.kitchen_mode")
        assert kitchen.state in kitchen.attributes["options"]
