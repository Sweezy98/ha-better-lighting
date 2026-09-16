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


class TestSceneFlow:
    async def _start(self, hass: HomeAssistant, entry, **overrides):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SubentryType.SCENE.value),
            context={"source": config_entries.SOURCE_USER},
        )
        return await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                "name": "Movie",
                "icon": "mdi:movie",
                "scene_zones": [],
                "override_mode": "both",
                "brightness_pct": 15,
                "color_format": "color_temp_kelvin",
                "transition": 2,
                "advanced": {},
                **overrides,
            },
        )

    async def _menu(self, hass: HomeAssistant, result, step: str, data=None):
        """Pick a menu option, or submit a form."""
        return await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {"next_step_id": step} if data is None else data,
        )

    async def test_two_step_flow_collects_the_colour(self, hass: HomeAssistant) -> None:
        entry = await setup_hub(hass, hub_entry())
        result = await self._start(hass, entry)
        assert result["step_id"] == "color"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"color_temp_kelvin": 2000}
        )
        # Colour done, now the per-light step.
        assert result["step_id"] == "lights"

        result = await self._menu(hass, result, "finish")
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["color_temp_kelvin"] == 2000

    async def test_brightness_only_scene_skips_the_colour_step(
        self, hass: HomeAssistant
    ) -> None:
        entry = await setup_hub(hass, hub_entry())
        result = await self._start(
            hass, entry, name="Task", override_mode="brightness", brightness_pct=100
        )
        # Nothing to ask: the scene does not own the colour axis.
        assert result["step_id"] == "lights"

        result = await self._menu(hass, result, "finish")
        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_no_colour_format_skips_the_colour_step(
        self, hass: HomeAssistant
    ) -> None:
        entry = await setup_hub(hass, hub_entry())
        result = await self._start(hass, entry, name="Plain", color_format="none")
        assert result["step_id"] == "lights"

        result = await self._menu(hass, result, "finish")
        assert result["type"] is FlowResultType.CREATE_ENTRY


class TestPerLightScene:
    """Naming individual lights inside a scene."""

    async def _flow_to_lights(self, hass: HomeAssistant, entry):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SubentryType.SCENE.value),
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                "name": "Movie",
                "icon": "mdi:movie",
                "scene_zones": [],
                "override_mode": "both",
                "brightness_pct": 15,
                "color_format": "none",
                "transition": 2,
                "advanced": {},
            },
        )
        assert result["step_id"] == "lights"
        return result

    async def _add(self, hass: HomeAssistant, result, light: str, **fields):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "add_light"}
        )
        assert result["step_id"] == "add_light"
        colour = fields.pop("colour", None)
        payload = {
            "light": light,
            "action": fields.pop("action", "apply"),
            "color_format": fields.pop("color_format", "inherit"),
        }
        payload |= fields
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], payload
        )
        if colour is not None:
            # The colour is asked for on its own, showing only the control
            # that was chosen.
            assert result["step_id"] == "light_color"
            result = await hass.config_entries.subentries.async_configure(
                result["flow_id"], colour
            )
        return result

    async def test_the_movie_scene_from_the_review(self, hass: HomeAssistant) -> None:
        """The exact scenario asked about: four lights, four different jobs."""
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(
            hass,
            hub_entry(
                subentries_data=[
                    zone_subentry("Living Room", ["light.one", "light.two"])
                ]
            ),
        )
        result = await self._flow_to_lights(hass, entry)

        # A strip behind the TV: dim, and a deliberate orange with no white.
        result = await self._add(
            hass,
            result,
            "light.one",
            brightness_pct=7,
            color_format="rgb_white",
            colour={"rgb_color": [255, 96, 16], "warm_white": 0, "cold_white": 0},
        )
        # A desk lamp: the scene's brightness, but its colour keeps adapting.
        result = await self._add(
            hass,
            result,
            "light.two",
            brightness_pct=20,
            color_format="none",
        )

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "finish"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY

        lights = result["data"]["lights"]
        assert lights["light.one"]["brightness_pct"] == 7
        assert lights["light.one"]["color_format"] == "rgb_white"
        assert lights["light.two"]["color_format"] == "none"

    async def test_it_renders_the_way_it_was_configured(
        self, hass: HomeAssistant
    ) -> None:
        """Straight through the real config path to real light entities."""
        await setup_members(
            hass,
            [
                MemberLight("Strip", is_on=True, brightness=200),
                MemberLight("Desk", is_on=True, brightness=200),
            ],
        )
        entry = await setup_hub(
            hass,
            hub_entry(
                subentries_data=[
                    zone_subentry("Living Room", ["light.strip", "light.desk"])
                ]
            ),
        )
        # Render adaptively first, so the captured value is what the curve
        # actually says rather than the fixture's own starting colour.
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.living_room"}, blocking=True
        )
        await hass.async_block_till_done()
        adaptive_kelvin = hass.states.get("light.desk").attributes["color_temp_kelvin"]

        result = await self._flow_to_lights(hass, entry)
        result = await self._add(
            hass,
            result,
            "light.strip",
            brightness_pct=7,
            color_format="color_temp_kelvin",
            colour={"color_temp_kelvin": 2200},
        )
        result = await self._add(
            hass,
            result,
            "light.desk",
            brightness_pct=20,
            color_format="none",
        )
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "finish"}
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.living_room_scenes", "option": "Movie"},
            blocking=True,
        )
        await hass.async_block_till_done()

        strip = hass.states.get("light.strip").attributes
        desk = hass.states.get("light.desk").attributes
        assert strip["brightness"] == pytest.approx(18, abs=2)
        assert strip["color_temp_kelvin"] == 2200
        assert desk["brightness"] == pytest.approx(51, abs=2)
        # The desk lamp kept the sun's colour rather than the scene's.
        assert desk["color_temp_kelvin"] == pytest.approx(adaptive_kelvin, abs=100)

    async def test_a_light_can_be_switched_off_by_a_scene(
        self, hass: HomeAssistant
    ) -> None:
        await setup_members(
            hass,
            [
                MemberLight("Strip", is_on=True, brightness=200),
                MemberLight("Ceiling", is_on=True, brightness=200),
            ],
        )
        entry = await setup_hub(
            hass,
            hub_entry(
                subentries_data=[
                    zone_subentry("Living Room", ["light.strip", "light.ceiling"])
                ]
            ),
        )
        result = await self._flow_to_lights(hass, entry)
        result = await self._add(hass, result, "light.ceiling", action="off")
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "finish"}
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.living_room_scenes", "option": "Movie"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert hass.states.get("light.ceiling").state == "off"
        # The rest of the room is still lit by the scene.
        assert hass.states.get("light.strip").state == "on"


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
            {"entity_id": "select.living_room_scenes", "option": "Reading"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert hass.states.get("light.lr").attributes["brightness"] < before
        assert hass.states.get("light.kt").attributes["brightness"] == before
        assert hass.states.get("select.kitchen_scenes").state == "Adaptive"

    async def test_an_unscoped_scene_is_offered_everywhere(
        self, hass: HomeAssistant
    ) -> None:
        """Right for a Night or Movie scene."""
        await self._two_rooms(hass)
        for room in ("living_room", "kitchen"):
            options = hass.states.get(f"select.{room}_scenes").attributes["options"]
            assert "Reading" in options

    async def test_a_scoped_scene_is_only_offered_there(
        self, hass: HomeAssistant
    ) -> None:
        await self._two_rooms(hass, scene_zones=["Living Room"])

        assert (
            "Reading"
            in hass.states.get("select.living_room_scenes").attributes["options"]
        )
        assert (
            "Reading"
            not in hass.states.get("select.kitchen_scenes").attributes["options"]
        )

    async def test_a_scene_can_be_scoped_to_several_rooms(
        self, hass: HomeAssistant
    ) -> None:
        await self._two_rooms(hass, scene_zones=["Living Room", "Kitchen"])
        for room in ("living_room", "kitchen"):
            assert (
                "Reading"
                in hass.states.get(f"select.{room}_scenes").attributes["options"]
            )

    async def test_an_active_scene_is_still_reported(self, hass: HomeAssistant) -> None:
        """Scoping hides a scene from the picker, not from the readout."""
        entry = await self._two_rooms(hass)
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.kitchen_scenes", "option": "Reading"},
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
        kitchen = hass.states.get("select.kitchen_scenes")
        assert kitchen.state in kitchen.attributes["options"]


class TestSceneTitles:
    """Home Assistant lists scenes flat, so the title has to do the organising."""

    async def _add_scene(self, hass: HomeAssistant, entry, name: str, zones: list[str]):
        ids = {sub.title: sub.subentry_id for sub in entry.subentries.values()}
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SubentryType.SCENE.value),
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                "name": name,
                "icon": "mdi:palette",
                "scene_zones": [ids[z] for z in zones],
                "override_mode": "brightness",
                "brightness_pct": 40,
                "color_format": "none",
                "transition": 1,
                "advanced": {},
            },
        )
        return await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "finish"}
        )

    async def _two_rooms(self, hass: HomeAssistant):
        await setup_members(hass, [MemberLight("A"), MemberLight("B")])
        return await setup_hub(
            hass,
            hub_entry(
                subentries_data=[
                    zone_subentry("Living Room", ["light.a"]),
                    zone_subentry("Bedroom", ["light.b"]),
                ]
            ),
        )

    async def test_a_one_room_scene_is_listed_under_its_room(
        self, hass: HomeAssistant
    ) -> None:
        entry = await self._two_rooms(hass)
        result = await self._add_scene(hass, entry, "Reading", ["Living Room"])
        assert result["title"] == "Living Room · Reading"

    async def test_two_rooms_can_both_have_a_reading_scene(
        self, hass: HomeAssistant
    ) -> None:
        """The point: neither has to be renamed to tell them apart."""
        entry = await self._two_rooms(hass)
        await self._add_scene(hass, entry, "Reading", ["Living Room"])
        await self._add_scene(hass, entry, "Reading", ["Bedroom"])

        titles = sorted(
            sub.title
            for sub in entry.subentries.values()
            if sub.subentry_type == SubentryType.SCENE.value
        )
        assert titles == ["Bedroom · Reading", "Living Room · Reading"]

    async def test_a_house_wide_scene_keeps_its_plain_name(
        self, hass: HomeAssistant
    ) -> None:
        entry = await self._two_rooms(hass)
        result = await self._add_scene(hass, entry, "Night", [])
        assert result["title"] == "Night"

    async def test_a_multi_room_scene_names_its_rooms(
        self, hass: HomeAssistant
    ) -> None:
        """Answers "which rooms is this one in?" from the list itself."""
        entry = await self._two_rooms(hass)
        result = await self._add_scene(hass, entry, "Movie", ["Living Room", "Bedroom"])
        assert result["title"] == "Movie · Bedroom, Living Room"

    async def test_the_room_prefix_does_not_leak_into_the_select(
        self, hass: HomeAssistant
    ) -> None:
        """Inside a room there is no ambiguity, so the plain name is shown."""
        entry = await self._two_rooms(hass)
        await self._add_scene(hass, entry, "Reading", ["Living Room"])
        await hass.async_block_till_done()

        options = hass.states.get("select.living_room_scenes").attributes["options"]
        assert "Reading" in options
        assert "Living Room · Reading" not in options
