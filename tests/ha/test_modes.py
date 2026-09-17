"""Cross-zone modes: the home-cinema scenario of requirement 2."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry, ConfigSubentryData
from homeassistant.core import HomeAssistant

from custom_components.better_lighting.const import DOMAIN, SubentryType
from tests.conftest import (
    MemberLight,
    hub_entry,
    setup_hub,
    setup_members,
    subentry_ids,
    zone_subentry,
)
from tests.ha.test_scenes import scene_subentry

CINEMA = "select.home_cinema_state"


def mode_subentry(
    name: str = "Home Cinema", rules=None, **overrides
) -> ConfigSubentryData:
    data = {
        "name": name,
        "icon": "mdi:movie-open",
        "states": ["playing", "paused"],
        "snapshot_on_enter": True,
        "restore_mode": "adaptive_on_previously_on",
        "opted_out_on_exit": "keep",
        "deferred_ttl_minutes": 240,
        "rules": rules or [],
        **overrides,
    }
    return ConfigSubentryData(
        data=data,
        subentry_type=SubentryType.MODE.value,
        title=name,
        unique_id=f"mode:{name.lower().replace(' ', '_')}",
    )


def rule(states, zones, action, **kw) -> dict:
    return {
        "mode_states": states,
        "zones": zones,
        "action": action,
        "scene_id": kw.get("scene_id"),
        "respect_presence": kw.get("respect_presence", True),
        "defer_if_occupied": kw.get("defer_if_occupied", True),
        "presence_entry_action": kw.get("presence_entry_action"),
        "presence_entry_scene": kw.get("presence_entry_scene"),
        "on_free_action": kw.get("on_free_action", "turn_off"),
        "scripts": kw.get("scripts", []),
        "enabled": kw.get("enabled", True),
    }


def _sub(data: ConfigSubentryData) -> ConfigSubentry:
    return ConfigSubentry(
        data=data["data"],
        subentry_type=data["subentry_type"],
        title=data["title"],
        unique_id=data["unique_id"],
    )


async def build(
    hass: HomeAssistant, *, presence: bool = False, rules_for=None, kitchen=None
):
    """A lounge and a kitchen, a Movie scene, and a cinema mode over both."""
    await setup_members(
        hass,
        [
            MemberLight("Lounge Main", is_on=True, brightness=200),
            MemberLight("Lounge Lamp", is_on=True, brightness=120),
            MemberLight("Kitchen Main", is_on=True, brightness=200),
        ],
    )
    kitchen_extra = (
        {"presence_entity": "binary_sensor.kitchen_presence", "presence_clear_delay": 0}
        if presence
        else {}
    )
    kitchen_extra |= kitchen or {}
    if presence:
        hass.states.async_set("binary_sensor.kitchen_presence", "off")

    entry = hub_entry(
        subentries_data=[
            zone_subentry("Lounge", ["light.lounge_main", "light.lounge_lamp"]),
            zone_subentry("Kitchen", ["light.kitchen_main"], **kitchen_extra),
            scene_subentry("Movie", brightness=5),
            scene_subentry("Path", brightness=15),
        ]
    )
    await setup_hub(hass, entry)

    ids = subentry_ids(entry)
    rules = (rules_for or _default_rules)(ids)
    hass.config_entries.async_add_subentry(entry, _sub(mode_subentry(rules=rules)))
    await hass.async_block_till_done()
    return entry, ids


def _default_rules(ids):
    return [
        rule(["playing"], [ids["Lounge"]], "apply_scene", scene_id=ids["Movie"]),
        rule(["playing"], [ids["Kitchen"]], "turn_off"),
    ]


async def set_state(hass: HomeAssistant, state: str) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": CINEMA, "option": state}, blocking=True
    )
    await hass.async_block_till_done()


class TestModeBasics:
    async def test_the_select_lists_idle_and_every_state(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        assert hass.states.get(CINEMA).attributes["options"] == [
            "off",
            "playing",
            "paused",
        ]
        assert hass.states.get(CINEMA).state == "off"

    async def test_starting_a_session_applies_the_rules(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        await set_state(hass, "playing")

        # The lounge dims to the movie scene; the kitchen goes dark.
        assert hass.states.get("light.lounge_main").attributes["brightness"] <= 20
        assert hass.states.get("light.kitchen_main").state == "off"
        assert hass.states.get(CINEMA).attributes["bl_session_id"] is not None

    async def test_a_room_with_no_rule_is_untouched(self, hass: HomeAssistant) -> None:
        await build(
            hass,
            rules_for=lambda ids: [rule(["playing"], [ids["Lounge"]], "turn_off")],
        )
        before = hass.states.get("light.kitchen_main").attributes["brightness"]
        await set_state(hass, "playing")
        assert hass.states.get("light.kitchen_main").attributes["brightness"] == before


class TestExit:
    async def test_only_previously_on_lights_return(self, hass: HomeAssistant) -> None:
        """The headline of requirement 2."""
        await setup_members(
            hass,
            [
                MemberLight("Lounge Main", is_on=True, brightness=200),
                MemberLight("Lounge Lamp", is_on=False),
                MemberLight("Kitchen Main", is_on=True, brightness=200),
            ],
        )
        entry = hub_entry(
            subentries_data=[
                zone_subentry("Lounge", ["light.lounge_main", "light.lounge_lamp"]),
                zone_subentry("Kitchen", ["light.kitchen_main"]),
                scene_subentry("Movie", brightness=5),
            ]
        )
        await setup_hub(hass, entry)
        ids = subentry_ids(entry)
        hass.config_entries.async_add_subentry(
            entry,
            _sub(
                mode_subentry(
                    rules=[
                        rule(
                            ["playing"],
                            [ids["Lounge"]],
                            "apply_scene",
                            scene_id=ids["Movie"],
                        ),
                        rule(["playing"], [ids["Kitchen"]], "turn_off"),
                    ]
                )
            ),
        )
        await hass.async_block_till_done()

        await set_state(hass, "playing")
        await set_state(hass, "off")

        # Lounge Lamp was off before the film, so it stays off.
        assert hass.states.get("light.lounge_main").state == "on"
        assert hass.states.get("light.lounge_lamp").state == "off"
        assert hass.states.get("light.kitchen_main").state == "on"

    async def test_rooms_return_to_adaptive_not_to_stored_brightness(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        await set_state(hass, "playing")
        await set_state(hass, "off")

        # Back under the curve, which is far brighter than the movie scene.
        assert hass.states.get("light.lounge_main").attributes["brightness"] > 100
        assert hass.states.get(CINEMA).attributes["bl_session_id"] is None


class TestStateChanges:
    async def test_the_snapshot_survives_a_state_change(
        self, hass: HomeAssistant
    ) -> None:
        """Re-snapshotting on pause would make the final restore relight nothing."""
        await build(
            hass,
            rules_for=lambda ids: [
                rule(["playing", "paused"], [ids["Kitchen"]], "turn_off")
            ],
        )
        await set_state(hass, "playing")
        taken = hass.states.get(CINEMA).attributes["bl_snapshot_taken_at"]
        assert hass.states.get("light.kitchen_main").state == "off"

        await set_state(hass, "paused")
        assert hass.states.get(CINEMA).attributes["bl_snapshot_taken_at"] == taken

        await set_state(hass, "off")
        # The kitchen was on before the film, so it comes back.
        assert hass.states.get("light.kitchen_main").state == "on"

    async def test_a_state_can_change_what_a_room_does(
        self, hass: HomeAssistant
    ) -> None:
        await build(
            hass,
            rules_for=lambda ids: [
                rule(
                    ["playing"], [ids["Lounge"]], "apply_scene", scene_id=ids["Movie"]
                ),
                rule(["paused"], [ids["Lounge"]], "adaptive"),
            ],
        )
        await set_state(hass, "playing")
        dim = hass.states.get("light.lounge_main").attributes["brightness"]

        await set_state(hass, "paused")
        assert hass.states.get("light.lounge_main").attributes["brightness"] > dim


class TestPresenceGating:
    async def test_an_occupied_room_is_not_darkened(self, hass: HomeAssistant) -> None:
        await build(hass, presence=True)
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()

        await set_state(hass, "playing")

        # Somebody is in there, so the light stays exactly as it was.
        assert hass.states.get("light.kitchen_main").state == "on"
        assert (
            "Kitchen" in str(hass.states.get(CINEMA).attributes["bl_deferred"])
            or (hass.states.get(CINEMA).attributes["bl_deferred"])
        )

    async def test_the_deferred_turn_off_fires_when_the_room_empties(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, presence=True)
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()
        await set_state(hass, "playing")
        assert hass.states.get("light.kitchen_main").state == "on"

        hass.states.async_set("binary_sensor.kitchen_presence", "off")
        await hass.async_block_till_done()

        assert hass.states.get("light.kitchen_main").state == "off"

    async def test_an_empty_room_is_darkened_immediately(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, presence=True)
        await set_state(hass, "playing")
        assert hass.states.get("light.kitchen_main").state == "off"

    async def test_the_deferred_action_dies_with_the_session(
        self, hass: HomeAssistant
    ) -> None:
        # The room's own presence rules are silenced here, so the only thing
        # that could darken it is the deferred action -- which is what this
        # test is actually about.
        await build(hass, presence=True, kitchen={"presence_off_action": "none"})
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()
        await set_state(hass, "playing")

        await set_state(hass, "off")
        hass.states.async_set("binary_sensor.kitchen_presence", "off")
        await hass.async_block_till_done()

        # The film ended first, so the room is never darkened after the fact.
        assert hass.states.get("light.kitchen_main").state == "on"

    async def test_walking_in_gets_the_entry_scene(self, hass: HomeAssistant) -> None:
        await build(
            hass,
            presence=True,
            rules_for=lambda ids: [
                rule(
                    ["playing"],
                    [ids["Kitchen"]],
                    "turn_off",
                    presence_entry_scene=ids["Path"],
                )
            ],
        )
        await set_state(hass, "playing")
        assert hass.states.get("light.kitchen_main").state == "off"

        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()

        # Lit, but dimly: the mode's entry scene rather than full brightness.
        state = hass.states.get("light.kitchen_main")
        assert state.state == "on"
        assert state.attributes["brightness"] <= 50

    async def test_leaving_again_darkens_the_room(self, hass: HomeAssistant) -> None:
        await build(
            hass,
            presence=True,
            rules_for=lambda ids: [
                rule(
                    ["playing"],
                    [ids["Kitchen"]],
                    "turn_off",
                    presence_entry_scene=ids["Path"],
                )
            ],
        )
        await set_state(hass, "playing")
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()
        assert hass.states.get("light.kitchen_main").state == "on"

        hass.states.async_set("binary_sensor.kitchen_presence", "off")
        await hass.async_block_till_done()
        assert hass.states.get("light.kitchen_main").state == "off"


class TestOptOut:
    async def test_a_press_takes_the_room_back(self, hass: HomeAssistant) -> None:
        """Requirement 2: press a switch during the film and that room is yours."""
        await build(hass)
        await set_state(hass, "playing")
        assert hass.states.get("light.kitchen_main").state == "off"

        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()

        assert hass.states.get("light.kitchen_main").state == "on"
        assert hass.states.get("select.kitchen_scenes").state == "Adaptive"
        assert hass.states.get(CINEMA).attributes["bl_opted_out"]

    async def test_an_opted_out_room_ignores_later_states(
        self, hass: HomeAssistant
    ) -> None:
        await build(
            hass,
            rules_for=lambda ids: [
                rule(["playing", "paused"], [ids["Kitchen"]], "turn_off")
            ],
        )
        await set_state(hass, "playing")
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()

        await set_state(hass, "paused")
        # The mode no longer speaks for this room.
        assert hass.states.get("light.kitchen_main").state == "on"

    async def test_an_opted_out_room_is_left_alone_at_the_end(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        await set_state(hass, "playing")
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()

        await set_state(hass, "off")
        # The user chose to leave it dark; the exit does not overrule that.
        assert hass.states.get("light.kitchen_main").state == "off"

    async def test_rejoining(self, hass: HomeAssistant) -> None:
        await build(hass)
        await set_state(hass, "playing")
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(CINEMA).attributes["bl_opted_out"]

        await hass.services.async_call(
            DOMAIN,
            "rejoin_mode",
            {"mode": "Home Cinema", "zone": "kitchen"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert not hass.states.get(CINEMA).attributes["bl_opted_out"]
        assert hass.states.get("light.kitchen_main").state == "off"


class TestServices:
    async def test_set_mode_and_end_mode(self, hass: HomeAssistant) -> None:
        await build(hass)
        await hass.services.async_call(
            DOMAIN,
            "set_mode",
            {"mode": "Home Cinema", "state": "playing"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert hass.states.get(CINEMA).state == "playing"

        await hass.services.async_call(
            DOMAIN, "end_mode", {"mode": "Home Cinema"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(CINEMA).state == "off"

    async def test_the_clear_button_ends_the_session(self, hass: HomeAssistant) -> None:
        await build(hass)
        await set_state(hass, "playing")
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.home_cinema_clear"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(CINEMA).state == "off"


class TestTheFullTrace:
    async def test_the_cinema_scenario_end_to_end(
        self, hass: HomeAssistant, freezer
    ) -> None:
        """The worked example from the requirement, start to finish."""
        await build(
            hass,
            presence=True,
            rules_for=lambda ids: [
                rule(
                    ["playing", "paused"],
                    [ids["Lounge"]],
                    "apply_scene",
                    scene_id=ids["Movie"],
                ),
                rule(
                    ["playing", "paused"],
                    [ids["Kitchen"]],
                    "turn_off",
                    presence_entry_scene=ids["Path"],
                ),
            ],
        )

        # Somebody is in the kitchen when the film starts.
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()
        await set_state(hass, "playing")

        assert hass.states.get("light.lounge_main").attributes["brightness"] <= 20
        assert hass.states.get("light.kitchen_main").state == "on", (
            "an occupied room must not be plunged into darkness"
        )

        # They leave; the deferred instruction finally runs.
        hass.states.async_set("binary_sensor.kitchen_presence", "off")
        await hass.async_block_till_done()
        assert hass.states.get("light.kitchen_main").state == "off"

        # The film is paused, then somebody wanders back in.
        await set_state(hass, "paused")
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()
        assert hass.states.get("light.kitchen_main").state == "on"

        # They press the kitchen switch: that room is theirs again.
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get("select.kitchen_scenes").state == "Adaptive"

        # The film ends. The lounge returns; the kitchen is left as they left it.
        await set_state(hass, "off")
        assert hass.states.get("light.lounge_main").attributes["brightness"] > 100
        assert hass.states.get("light.kitchen_main").state == "on"


class TestEnableToggle:
    """Switching a mode off mutes us, not the automation driving it."""

    ENABLED = "switch.home_cinema_enabled"

    async def _set_enabled(self, hass: HomeAssistant, on: bool) -> None:
        await hass.services.async_call(
            "switch",
            "turn_on" if on else "turn_off",
            {"entity_id": self.ENABLED},
            blocking=True,
        )
        await hass.async_block_till_done()

    async def test_a_disabled_mode_leaves_the_rooms_alone(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        await self._set_enabled(hass, False)
        before = hass.states.get("light.lounge_main").attributes["brightness"]

        await set_state(hass, "playing")

        assert hass.states.get("light.lounge_main").attributes["brightness"] == before
        assert hass.states.get("light.kitchen_main").state == "on"
        assert hass.states.get(CINEMA).attributes["bl_session_id"] is None

    async def test_the_state_is_still_tracked_while_disabled(
        self, hass: HomeAssistant
    ) -> None:
        """The whole point: automations fire on changes, so we must remember."""
        await build(hass)
        await self._set_enabled(hass, False)
        await set_state(hass, "playing")

        assert hass.states.get(CINEMA).state == "playing"
        assert hass.states.get(self.ENABLED).attributes["bl_tracked_state"] == "playing"

    async def test_enabling_mid_film_picks_up_the_tracked_state(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        await self._set_enabled(hass, False)
        await set_state(hass, "playing")

        await self._set_enabled(hass, True)

        assert hass.states.get("light.lounge_main").attributes["brightness"] <= 20
        assert hass.states.get("light.kitchen_main").state == "off"
        assert hass.states.get(CINEMA).attributes["bl_session_id"] is not None

    async def test_disabling_mid_session_puts_the_rooms_back(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        await set_state(hass, "playing")
        assert hass.states.get("light.kitchen_main").state == "off"

        await self._set_enabled(hass, False)

        assert hass.states.get("light.kitchen_main").state == "on"
        assert hass.states.get(CINEMA).attributes["bl_session_id"] is None
        # The film has not stopped, so the state stays -- and re-enabling
        # returns to it rather than waiting for the automation's next trigger.
        assert hass.states.get(CINEMA).state == "playing"

    async def test_the_film_ending_while_disabled_clears_the_state(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass)
        await self._set_enabled(hass, False)
        await set_state(hass, "playing")
        await set_state(hass, "off")

        assert hass.states.get(CINEMA).state == "off"
        await self._set_enabled(hass, True)
        assert hass.states.get(CINEMA).attributes["bl_session_id"] is None


class TestReEntryActions:
    """Walking back in gets the same four choices as the state change."""

    async def _build(self, hass: HomeAssistant, **entry):
        def rules_for(ids):
            return [
                rule(
                    ["playing"],
                    [ids["Kitchen"]],
                    entry.get("mode_action", "turn_off"),
                    presence_entry_action=entry.get("action"),
                    presence_entry_scene=(
                        ids[entry["scene"]] if entry.get("scene") else None
                    ),
                )
            ]

        return await build(hass, presence=True, rules_for=rules_for)

    async def _enter(self, hass: HomeAssistant) -> None:
        hass.states.async_set("binary_sensor.kitchen_presence", "on")
        await hass.async_block_till_done()

    async def test_entering_can_apply_a_scene(self, hass: HomeAssistant) -> None:
        await self._build(hass, action="apply_scene", scene="Path")
        await set_state(hass, "playing")
        assert hass.states.get("light.kitchen_main").state == "off"

        await self._enter(hass)

        assert hass.states.get("light.kitchen_main").state == "on"

    async def test_entering_can_return_the_room_to_adaptive(
        self, hass: HomeAssistant
    ) -> None:
        await self._build(hass, action="adaptive")
        await set_state(hass, "playing")

        await self._enter(hass)

        assert hass.states.get("light.kitchen_main").state == "on"

    async def test_entering_can_do_nothing(self, hass: HomeAssistant) -> None:
        await self._build(hass, action="keep")
        await set_state(hass, "playing")

        await self._enter(hass)

        assert hass.states.get("light.kitchen_main").state == "off"

    async def test_entering_can_switch_the_room_off(self, hass: HomeAssistant) -> None:
        """Odd for a cinema, but it is what a utility room might want."""
        await self._build(hass, action="turn_off", mode_action="keep")
        await set_state(hass, "playing")
        assert hass.states.get("light.kitchen_main").state == "on"

        await self._enter(hass)

        assert hass.states.get("light.kitchen_main").state == "off"


class TestScripts:
    """A film is not only a lighting change.

    The amplifier, the blinds, a notification: a rule already knows when the
    mode reaches a state, so it is the right place to say what else happens.
    """

    @staticmethod
    def _watch(hass: HomeAssistant) -> list[dict]:
        calls: list[dict] = []

        async def _record(call):
            calls.append(dict(call.data))

        hass.services.async_register("script", "turn_on", _record)
        return calls

    @staticmethod
    def _rules(ids):
        return [
            rule(
                ["playing"],
                [ids["Lounge"], ids["Kitchen"]],
                "apply_scene",
                scene_id=ids["Movie"],
                scripts=["script.dim_the_amplifier"],
            ),
            rule(["off"], [ids["Lounge"]], "keep", scripts=["script.lights_up"]),
        ]

    async def test_a_rule_runs_its_scripts_once_however_many_rooms(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, rules_for=self._rules)
        calls = self._watch(hass)

        await set_state(hass, "playing")

        assert calls == [{"entity_id": ["script.dim_the_amplifier"]}]

    async def test_the_end_of_a_session_runs_the_off_rule(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, rules_for=self._rules)
        await set_state(hass, "playing")
        calls = self._watch(hass)

        await set_state(hass, "off")

        assert calls == [{"entity_id": ["script.lights_up"]}]

    async def test_switching_the_mode_off_mid_film_runs_them_too(
        self, hass: HomeAssistant
    ) -> None:
        """The rooms go back to how they were, so everything else should too."""
        await build(hass, rules_for=self._rules)
        await set_state(hass, "playing")
        calls = self._watch(hass)

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.home_cinema_enabled"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert calls == [{"entity_id": ["script.lights_up"]}]

    async def test_a_rule_with_no_room_still_runs(self, hass: HomeAssistant) -> None:
        """A rule that only runs scripts is about the house.

        Demanding a room for it meant naming one at random and hoping nothing
        else was configured for that room -- and a rule left without one
        matched nothing, so it quietly did nothing at all.
        """

        def rules(ids):
            return [
                rule([], [], "keep", scripts=["script.start_the_projector"]),
                rule(["off"], [], "keep", scripts=["script.projector_off"]),
            ]

        # A rule naming no state either would never fire, so the first one
        # names the state it belongs to.
        def with_states(ids):
            found = rules(ids)
            found[0]["mode_states"] = ["playing"]
            return found

        await build(hass, rules_for=with_states)
        calls = self._watch(hass)

        await set_state(hass, "playing")
        assert calls == [{"entity_id": ["script.start_the_projector"]}]

        calls.clear()
        await set_state(hass, "off")
        assert calls == [{"entity_id": ["script.projector_off"]}]

    async def test_a_mode_that_never_started_runs_nothing(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, rules_for=self._rules)
        calls = self._watch(hass)

        await set_state(hass, "off")

        assert calls == []


class TestSwitchedOffActions:
    """An action that is written down but not happening.

    Which is how somebody finds out whether a particular one is what makes
    their film behave oddly, without writing it out and writing it back.
    """

    @staticmethod
    def _rules(ids):
        return [
            rule(
                ["playing"],
                [ids["Kitchen"]],
                "turn_off",
                enabled=False,
            ),
            rule(["playing"], [ids["Lounge"]], "turn_off"),
        ]

    async def test_it_does_not_happen(self, hass: HomeAssistant) -> None:
        await build(hass, rules_for=self._rules)

        await set_state(hass, "playing")

        # The one that is on darkened its room; the one that is off did not.
        assert hass.states.get("light.lounge_main").state == "off"
        assert hass.states.get("light.kitchen_main").state == "on"

    async def test_a_switched_off_house_action_runs_nothing(
        self, hass: HomeAssistant
    ) -> None:
        def rules(ids):
            return [
                rule(
                    ["playing"],
                    [],
                    "keep",
                    scripts=["script.projector"],
                    enabled=False,
                )
            ]

        await build(hass, rules_for=rules)
        calls = TestScripts._watch(hass)

        await set_state(hass, "playing")

        assert calls == []
