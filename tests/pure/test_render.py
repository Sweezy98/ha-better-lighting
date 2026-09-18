"""The render pipeline. Pure: no hass fixture, no event loop."""

from __future__ import annotations

import datetime as dt

import pytest

from custom_components.better_lighting.adaptive import AdaptiveSettings
from custom_components.better_lighting.profiles import (
    Axis,
    LightCapabilities,
    LightProfile,
)
from custom_components.better_lighting.render import (
    LightSnapshot,
    RenderRequest,
    RoomMode,
    Trigger,
    batch,
    emitted_axes,
    render_room,
)
from custom_components.better_lighting.scenes import (
    OthersPolicy,
    Scene,
    SceneLightSpec,
    SceneOverride,
    UnsupportedColorPolicy,
)

SETTINGS = AdaptiveSettings(
    at=dt.datetime(2026, 6, 21, 12, tzinfo=dt.UTC),
    brightness_pct=60.0,
    color_temp_kelvin=4000,
    sun_position=0.5,
    is_night=False,
    rgb_color=(255, 200, 150),
    xy_color=(0.4, 0.4),
    hs_color=(30.0, 40.0),
)


def caps(entity_id: str, modes: set[str] | None = None) -> LightCapabilities:
    return LightCapabilities.from_attributes(
        entity_id,
        {
            "supported_color_modes": list(modes or {"color_temp", "hs"}),
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6500,
        },
        supports_transition=True,
    )


def member(entity_id: str, *, on: bool = True, modes=None, available=True):
    return LightSnapshot(
        entity_id=entity_id,
        is_on=on,
        available=available,
        caps=caps(entity_id, modes),
        brightness=120 if on else None,
    )


def request(**overrides) -> RenderRequest:
    base = {
        "mode": RoomMode.ADAPTIVE,
        "trigger": Trigger.ACTIVATE,
        "settings": SETTINGS,
        "members": [member("light.one"), member("light.two")],
    }
    return RenderRequest(**{**base, **overrides})


def by_entity(result) -> dict[str, dict]:
    return {c.entity_id: c.data for c in result.commands if c.action == "turn_on"}


def off_entities(result) -> set[str]:
    return {c.entity_id for c in result.commands if c.action == "turn_off"}


class TestEmittedAxes:
    """The function that is the whole partial-adaptive mechanism."""

    def test_tick_emits_only_the_adaptive_axes(self):
        assert emitted_axes(Trigger.TICK, Axis.BRIGHTNESS, Axis.COLOR) is Axis.COLOR

    def test_activate_emits_both_sources(self):
        assert emitted_axes(Trigger.ACTIVATE, Axis.BRIGHTNESS, Axis.COLOR) is Axis.ALL

    def test_dim_emits_only_brightness(self):
        assert emitted_axes(Trigger.DIM, Axis.COLOR, Axis.BRIGHTNESS) is Axis.BRIGHTNESS

    def test_dim_emits_nothing_when_brightness_is_not_owned(self):
        assert emitted_axes(Trigger.DIM, Axis.COLOR, Axis.NONE) is Axis.NONE


class TestSceneOverrideModes:
    """Requirement 7, all four modes."""

    def test_both_overrides_brightness_and_colour(self):
        scene = Scene(
            "s",
            override=SceneOverride.BOTH,
            brightness_pct=20,
            color={"color_temp_kelvin": 2200},
        )
        data = by_entity(render_room(request(mode=RoomMode.SCENE, scene=scene)))
        assert data["light.one"]["color_temp_kelvin"] == 2200
        # 20% of 255 is 51.
        assert data["light.one"]["brightness"] == pytest.approx(51, abs=1)

    def test_brightness_mode_keeps_the_adaptive_colour(self):
        scene = Scene(
            "s",
            override=SceneOverride.BRIGHTNESS,
            brightness_pct=20,
            color={"color_temp_kelvin": 2200},
        )
        data = by_entity(render_room(request(mode=RoomMode.SCENE, scene=scene)))
        assert data["light.one"]["brightness"] == pytest.approx(51, abs=1)
        # The scene's colour is ignored; the curve's is used.
        assert data["light.one"]["color_temp_kelvin"] == 4000

    def test_colour_mode_keeps_the_adaptive_brightness(self):
        scene = Scene(
            "s",
            override=SceneOverride.COLOR,
            brightness_pct=20,
            color={"color_temp_kelvin": 2200},
        )
        data = by_entity(render_room(request(mode=RoomMode.SCENE, scene=scene)))
        assert data["light.one"]["color_temp_kelvin"] == 2200
        # 60% of 255 is 153: the curve's brightness, not the scene's.
        assert data["light.one"]["brightness"] == pytest.approx(153, abs=1)

    def test_neither_mode_leaves_both_adaptive(self):
        scene = Scene(
            "s",
            override=SceneOverride.NEITHER,
            brightness_pct=20,
            color={"color_temp_kelvin": 2200},
        )
        data = by_entity(render_room(request(mode=RoomMode.SCENE, scene=scene)))
        assert data["light.one"]["color_temp_kelvin"] == 4000
        assert data["light.one"]["brightness"] == pytest.approx(153, abs=1)


class TestPartialAdaptiveTick:
    """The subtle half of requirement 7: the unclaimed axis keeps updating."""

    def test_brightness_scene_on_tick_sends_only_colour(self):
        scene = Scene(
            "s",
            override=SceneOverride.BRIGHTNESS,
            brightness_pct=20,
            color={"color_temp_kelvin": 2200},
        )
        data = by_entity(
            render_room(request(mode=RoomMode.SCENE, scene=scene, trigger=Trigger.TICK))
        )
        assert "color_temp_kelvin" in data["light.one"]
        assert "brightness" not in data["light.one"], (
            "the scene owns brightness, so a tick must not re-send it"
        )

    def test_colour_scene_on_tick_sends_only_brightness(self):
        scene = Scene(
            "s",
            override=SceneOverride.COLOR,
            brightness_pct=20,
            color={"color_temp_kelvin": 2200},
        )
        data = by_entity(
            render_room(request(mode=RoomMode.SCENE, scene=scene, trigger=Trigger.TICK))
        )
        assert "brightness" in data["light.one"]
        assert "color_temp_kelvin" not in data["light.one"]

    def test_both_scene_on_tick_sends_nothing(self):
        scene = Scene(
            "s",
            override=SceneOverride.BOTH,
            brightness_pct=20,
            color={"color_temp_kelvin": 2200},
        )
        result = render_room(
            request(mode=RoomMode.SCENE, scene=scene, trigger=Trigger.TICK)
        )
        assert result.commands == []

    def test_activate_sends_both_in_one_command(self):
        scene = Scene("s", override=SceneOverride.BRIGHTNESS, brightness_pct=20)
        data = by_entity(
            render_room(
                request(mode=RoomMode.SCENE, scene=scene, trigger=Trigger.ACTIVATE)
            )
        )
        assert {"brightness", "color_temp_kelvin"} <= set(data["light.one"])


class TestTickInvariant:
    """A tick adjusts what is lit. It never changes on/off state."""

    def test_tick_ignores_dark_lights(self):
        result = render_room(
            request(
                members=[member("light.one", on=True), member("light.two", on=False)],
                trigger=Trigger.TICK,
            )
        )
        assert set(by_entity(result)) == {"light.one"}

    def test_tick_never_turns_anything_off(self):
        scene = Scene(
            "s", lights={"light.one": SceneLightSpec()}, others=OthersPolicy.OFF
        )
        result = render_room(
            request(mode=RoomMode.SCENE, scene=scene, trigger=Trigger.TICK)
        )
        assert off_entities(result) == set()

    @pytest.mark.parametrize("mode", list(RoomMode))
    def test_no_mode_makes_a_tick_switch_a_dark_light_on(self, mode):
        result = render_room(
            request(
                mode=mode,
                trigger=Trigger.TICK,
                members=[member("light.one", on=False)],
                scene=Scene("s", brightness_pct=50) if mode is RoomMode.SCENE else None,
            )
        )
        assert by_entity(result) == {}


class TestOnLightsOnly:
    """Requirement 6."""

    def test_dark_lights_get_no_commands_at_all(self):
        scene = Scene("s", brightness_pct=30, on_lights_only=True)
        result = render_room(
            request(
                mode=RoomMode.SCENE,
                scene=scene,
                members=[member("light.one", on=True), member("light.two", on=False)],
            )
        )
        assert set(by_entity(result)) == {"light.one"}
        assert off_entities(result) == set()

    def test_without_the_flag_dark_lights_are_lit(self):
        scene = Scene("s", brightness_pct=30, on_lights_only=False)
        result = render_room(
            request(
                mode=RoomMode.SCENE,
                scene=scene,
                members=[member("light.one", on=True), member("light.two", on=False)],
            )
        )
        assert set(by_entity(result)) == {"light.one", "light.two"}


class TestOthersPolicy:
    def test_unnamed_members_keep_adapting_by_default(self):
        scene = Scene("s", brightness_pct=20, lights={"light.one": SceneLightSpec()})
        data = by_entity(render_room(request(mode=RoomMode.SCENE, scene=scene)))
        # light.two is not in the scene but is on, so it keeps the curve.
        assert data["light.two"]["brightness"] == pytest.approx(153, abs=1)

    def test_unnamed_members_can_be_switched_off(self):
        scene = Scene(
            "s",
            brightness_pct=20,
            lights={"light.one": SceneLightSpec()},
            others=OthersPolicy.OFF,
        )
        result = render_room(request(mode=RoomMode.SCENE, scene=scene))
        assert off_entities(result) == {"light.two"}

    def test_unnamed_members_can_be_left_alone(self):
        scene = Scene(
            "s",
            brightness_pct=20,
            lights={"light.one": SceneLightSpec()},
            others=OthersPolicy.LEAVE,
        )
        result = render_room(request(mode=RoomMode.SCENE, scene=scene))
        assert "light.two" not in by_entity(result)
        assert off_entities(result) == set()
        assert result.skipped["light.two"] == "others_leave"


class TestManualOverride:
    def test_a_manual_axis_is_not_touched(self):
        data = by_entity(render_room(request(manual={"light.one": Axis.BRIGHTNESS})))
        assert "brightness" not in data["light.one"]
        assert "color_temp_kelvin" in data["light.one"]

    def test_a_fully_manual_light_is_skipped(self):
        result = render_room(request(manual={"light.one": Axis.ALL}))
        assert "light.one" not in by_entity(result)
        assert result.skipped["light.one"] == "manual"

    def test_manual_is_per_light(self):
        """The regression test for Adaptive Lighting's global manual dict."""
        data = by_entity(render_room(request(manual={"light.one": Axis.ALL})))
        assert "light.two" in data

    def test_a_scene_cannot_override_a_manual_axis(self):
        scene = Scene(
            "s",
            override=SceneOverride.BOTH,
            brightness_pct=20,
            color={"color_temp_kelvin": 2200},
        )
        data = by_entity(
            render_room(
                request(
                    mode=RoomMode.SCENE,
                    scene=scene,
                    manual={"light.one": Axis.COLOR},
                )
            )
        )
        assert "color_temp_kelvin" not in data["light.one"]
        assert data["light.one"]["brightness"] == pytest.approx(51, abs=1)


class TestModes:
    def test_off_switches_every_lit_member_off(self):
        result = render_room(
            request(
                mode=RoomMode.OFF,
                members=[member("light.one", on=True), member("light.two", on=False)],
            )
        )
        assert off_entities(result) == {"light.one"}

    def test_external_emits_nothing(self):
        """Another subsystem is driving; observe but do not command."""
        result = render_room(request(mode=RoomMode.EXTERNAL))
        assert result.commands == []

    def test_unavailable_members_are_ignored(self):
        result = render_room(
            request(members=[member("light.one", available=False), member("light.two")])
        )
        assert set(by_entity(result)) == {"light.two"}


class TestColourReconciliation:
    def test_colour_temp_scene_on_an_rgb_only_light(self):
        scene = Scene(
            "s", override=SceneOverride.COLOR, color={"color_temp_kelvin": 2200}
        )
        data = by_entity(
            render_room(
                request(
                    mode=RoomMode.SCENE,
                    scene=scene,
                    members=[member("light.one", modes={"rgb"})],
                )
            )
        )
        assert "rgb_color" in data["light.one"]

    def test_chromatic_scene_on_a_ct_only_light_stays_adaptive(self):
        """The default: a bulb that cannot do colour keeps its adaptive white."""
        scene = Scene("s", override=SceneOverride.COLOR, color={"hs_color": (280, 90)})
        data = by_entity(
            render_room(
                request(
                    mode=RoomMode.SCENE,
                    scene=scene,
                    members=[member("light.one", modes={"color_temp"})],
                )
            )
        )
        assert data["light.one"]["color_temp_kelvin"] == 4000

    def test_nearest_ct_policy_projects_onto_white(self):
        scene = Scene(
            "s",
            override=SceneOverride.COLOR,
            color={"hs_color": (30, 90)},
            on_unsupported_color=UnsupportedColorPolicy.NEAREST_CT,
        )
        data = by_entity(
            render_room(
                request(
                    mode=RoomMode.SCENE,
                    scene=scene,
                    members=[member("light.one", modes={"color_temp"})],
                )
            )
        )
        # A warm orange should project to the warm end, not stay at 4000 K.
        assert data["light.one"]["color_temp_kelvin"] < 4000


class TestPerLightSpecs:
    def test_a_light_can_override_the_scene_brightness(self):
        scene = Scene(
            "s",
            brightness_pct=50,
            lights={
                "light.one": SceneLightSpec(brightness_pct=10),
                "light.two": SceneLightSpec(),
            },
        )
        data = by_entity(render_room(request(mode=RoomMode.SCENE, scene=scene)))
        assert data["light.one"]["brightness"] < data["light.two"]["brightness"]

    def test_partially_specified_light_keeps_the_other_axis_adaptive(self):
        """A BOTH scene that only names a colour leaves brightness adaptive."""
        scene = Scene(
            "s",
            override=SceneOverride.BOTH,
            lights={"light.one": SceneLightSpec(color={"color_temp_kelvin": 2200})},
        )
        data = by_entity(render_room(request(mode=RoomMode.SCENE, scene=scene)))
        assert data["light.one"]["color_temp_kelvin"] == 2200
        assert data["light.one"]["brightness"] == pytest.approx(153, abs=1)

    def test_a_light_can_be_skipped(self):
        scene = Scene(
            "s",
            brightness_pct=20,
            lights={
                "light.one": SceneLightSpec(skip=True),
                "light.two": SceneLightSpec(),
            },
            others=OthersPolicy.LEAVE,
        )
        result = render_room(request(mode=RoomMode.SCENE, scene=scene))
        assert "light.one" not in by_entity(result)


class TestProfileInteraction:
    def test_a_light_profile_limits_a_scene_too(self):
        """A limit protects the user from the scene as much as from the curve."""
        scene = Scene("s", brightness_pct=100)
        data = by_entity(
            render_room(
                request(
                    mode=RoomMode.SCENE,
                    scene=scene,
                    profiles={"light.one": LightProfile(max_brightness_pct=10)},
                )
            )
        )
        assert data["light.one"]["brightness"] <= 27

    def test_bias_shifts_a_scene_brightness(self):
        scene = Scene("s", brightness_pct=50)
        plain = by_entity(render_room(request(mode=RoomMode.SCENE, scene=scene)))
        dimmed = by_entity(
            render_room(request(mode=RoomMode.SCENE, scene=scene, bias_pct=-20))
        )
        assert dimmed["light.one"]["brightness"] < plain["light.one"]["brightness"]


class TestBatching:
    def test_identical_payloads_share_one_call(self):
        result = render_room(request())
        calls = batch(result.commands)
        assert len(calls) == 1
        assert calls[0][1]["entity_id"] == ["light.one", "light.two"]

    def test_differing_payloads_are_separate(self):
        result = render_room(
            request(profiles={"light.one": LightProfile(brightness_offset_pct=-30)})
        )
        assert len(batch(result.commands)) == 2

    def test_turn_on_and_turn_off_never_merge(self):
        scene = Scene(
            "s",
            brightness_pct=20,
            lights={"light.one": SceneLightSpec()},
            others=OthersPolicy.OFF,
        )
        calls = batch(render_room(request(mode=RoomMode.SCENE, scene=scene)).commands)
        actions = {action for action, _ in calls}
        assert actions == {"turn_on", "turn_off"}
