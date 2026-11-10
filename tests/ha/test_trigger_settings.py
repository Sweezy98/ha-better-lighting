"""Each trigger's own say: its rules, its night, its scene."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from tests.conftest import (
    MemberLight,
    hub_entry,
    room_subentry,
    setup_hub,
    setup_members,
)

NIGHT = "input_boolean.asleep"
BED = "binary_sensor.bed_presence"
PIR = "binary_sensor.under_bed"
HALL = "binary_sensor.hallway_motion"
LOUNGE = "binary_sensor.lounge_motion"
TV = "media_player.tv"

# One lamp, and nothing else: the way to the door at three in the morning.
TOILET = {
    "scene_id": "scene_toilet",
    "name": "Toilet",
    "others": "off",
    "lights": {"light.two": {"action": "apply", "brightness_pct": 5}},
}


async def build(hass: HomeAssistant, triggers: list[dict], **room) -> None:
    hass.states.async_set(NIGHT, "off")
    for trigger in triggers:
        hass.states.async_set(trigger["trigger_entity"], "off")
    await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
    entry = hub_entry(
        options={"night_source_entity": NIGHT},
        subentries_data=[
            room_subentry(
                triggers=triggers,
                presence_clear_delay=0,
                scenes=[TOILET],
                **({"night_behavior": "min_settings"} | room),
            ),
        ],
    )
    await setup_hub(hass, entry)


async def set_state(hass: HomeAssistant, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done()


def lit(hass: HomeAssistant, entity_id: str = "light.one") -> bool:
    return hass.states.get(entity_id).state == "on"


class TestNightPerTrigger:
    async def test_one_sensor_stands_aside_while_the_other_still_lights(
        self, hass: HomeAssistant
    ) -> None:
        """The bedroom: the presence sensor over the bed is ignored at night,
        the one under it still lights the way to the toilet."""
        await build(
            hass,
            [
                {"trigger_entity": BED, "ignore_at_night": True},
                {"trigger_entity": PIR, "trigger_scene_id": "scene_toilet"},
            ],
        )
        await set_state(hass, NIGHT, "on")

        await set_state(hass, BED, "on")
        assert not lit(hass, "light.one") and not lit(hass, "light.two")

        await set_state(hass, PIR, "on")
        # The toilet scene: only the lamp it names.
        assert lit(hass, "light.two")
        assert not lit(hass, "light.one")

        # And the sensor over the bed, still saying somebody is there, does not
        # keep it on once the one under the bed goes quiet.
        await set_state(hass, PIR, "off")
        assert not lit(hass, "light.two")

    async def test_night_beginning_lets_go_of_an_ignored_sensor(
        self, hass: HomeAssistant
    ) -> None:
        await build(
            hass,
            [
                {"trigger_entity": BED, "ignore_at_night": True},
                {"trigger_entity": PIR},
            ],
        )
        await set_state(hass, BED, "on")
        assert lit(hass)

        await set_state(hass, NIGHT, "on")
        # Lights are left as they are; the room is simply no longer held.
        assert lit(hass)
        await set_state(hass, PIR, "on")
        await set_state(hass, PIR, "off")
        assert not lit(hass)

    async def test_a_room_that_ignored_presence_at_night_still_does(
        self, hass: HomeAssistant
    ) -> None:
        """Stored before triggers could say: the room's flag is their default."""
        await build(hass, [{"trigger_entity": BED}], night_ignore_presence=True)
        await set_state(hass, NIGHT, "on")
        await set_state(hass, BED, "on")
        assert not lit(hass)


class TestRulesPerTrigger:
    async def test_the_hallway_lights_the_lounge_only_while_night_is_off(
        self, hass: HomeAssistant
    ) -> None:
        night_off = {
            "condition_id": "night_off",
            "check": "state_is",
            "condition_entity": NIGHT,
            "required_state": "off",
        }
        await build(
            hass,
            [
                {"trigger_entity": LOUNGE},
                {"trigger_entity": HALL, "rules": [night_off]},
            ],
        )
        await set_state(hass, HALL, "on")
        assert lit(hass)
        await set_state(hass, HALL, "off")
        assert not lit(hass)

        await set_state(hass, NIGHT, "on")
        await set_state(hass, HALL, "on")
        assert not lit(hass)
        # The lounge's own sensor does not care.
        await set_state(hass, LOUNGE, "on")
        assert lit(hass)

    async def test_the_room_rules_still_hold_for_every_trigger(
        self, hass: HomeAssistant
    ) -> None:
        await build(
            hass,
            [{"trigger_entity": LOUNGE}],
            rules=[
                {
                    "condition_id": "armed",
                    "check": "state_is",
                    "condition_entity": "input_boolean.armed",
                    "required_state": "on",
                }
            ],
        )
        hass.states.async_set("input_boolean.armed", "off")
        await set_state(hass, LOUNGE, "on")
        assert not lit(hass)


class TestMediaPlayers:
    async def test_a_playing_tv_is_somebody_in_the_room(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, [{"trigger_entity": TV}])
        await set_state(hass, TV, "playing")
        assert lit(hass)
        await set_state(hass, TV, "idle")
        assert not lit(hass)


class TestActivity:
    async def test_presence_and_its_outcome_are_logged(
        self, hass: HomeAssistant
    ) -> None:
        seen = []
        hass.bus.async_listen("better_lighting_activity", seen.append)
        await build(hass, [{"trigger_entity": LOUNGE}])
        await set_state(hass, LOUNGE, "on")

        detected = [e.data for e in seen if e.data["what"] == "presence_detected"]
        assert detected and detected[-1]["trigger"] == LOUNGE
        assert detected[-1]["outcome"].startswith("lit:")
        lights = [e.data for e in seen if e.data["what"] == "lights"]
        assert lights and lights[-1]["cause"] == "presence"


class TestNightScene:
    async def test_a_night_scene_lights_only_the_lights_it_names(
        self, hass: HomeAssistant
    ) -> None:
        await build(
            hass,
            [{"trigger_entity": LOUNGE}],
            night_behavior="scene",
            night_scene_id="scene_toilet",
        )
        await set_state(hass, NIGHT, "on")
        await set_state(hass, LOUNGE, "on")
        assert lit(hass, "light.two")
        assert not lit(hass, "light.one")


GUESTS = "input_boolean.guests"


async def build_lounge(hass: HomeAssistant, **trigger) -> None:
    """A living room whose sofa is a bed on the nights guests stay."""
    hass.states.async_set(NIGHT, "off")
    hass.states.async_set(GUESTS, "off")
    hass.states.async_set(LOUNGE, "off")
    await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
    entry = hub_entry(
        options={"night_source_entity": NIGHT, "guest_source_entity": GUESTS},
        subentries_data=[
            room_subentry(
                triggers=[{"trigger_entity": LOUNGE, **trigger}],
                presence_clear_delay=0,
                night_behavior="min_settings",
            ),
        ],
    )
    await setup_hub(hass, entry)


class TestGuests:
    async def test_without_guests_the_lounge_still_lights_at_night(
        self, hass: HomeAssistant
    ) -> None:
        await build_lounge(hass, ignore_at_night_with_guests=True)
        await set_state(hass, NIGHT, "on")
        await set_state(hass, LOUNGE, "on")
        assert lit(hass)

    async def test_with_guests_it_stands_aside_like_a_bedroom(
        self, hass: HomeAssistant
    ) -> None:
        await build_lounge(hass, ignore_at_night_with_guests=True)
        await set_state(hass, GUESTS, "on")
        await set_state(hass, NIGHT, "on")
        await set_state(hass, LOUNGE, "on")
        assert not lit(hass)

    async def test_by_day_guests_change_nothing(self, hass: HomeAssistant) -> None:
        await build_lounge(hass, ignore_at_night_with_guests=True)
        await set_state(hass, GUESTS, "on")
        await set_state(hass, LOUNGE, "on")
        assert lit(hass)

    async def test_guests_arriving_mid_night_let_go_of_the_sensor(
        self, hass: HomeAssistant
    ) -> None:
        await build_lounge(hass, ignore_at_night_with_guests=True)
        await set_state(hass, NIGHT, "on")
        await set_state(hass, LOUNGE, "on")
        assert lit(hass)

        await set_state(hass, GUESTS, "on")
        # Nothing switched off, but the room is no longer held: quiet, then
        # active again, and it is ignored.
        await set_state(hass, LOUNGE, "off")
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()
        await set_state(hass, LOUNGE, "on")
        assert not lit(hass)


class TestPresenceSwitch:
    async def test_switched_off_the_sensor_lights_nothing(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, [{"trigger_entity": LOUNGE}])
        assert hass.states.get("switch.kitchen_presence_detection").state == "on"

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.kitchen_presence_detection"},
            blocking=True,
        )
        await set_state(hass, LOUNGE, "on")
        assert not lit(hass)

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.kitchen_presence_detection"},
            blocking=True,
        )
        await set_state(hass, LOUNGE, "off")
        await set_state(hass, LOUNGE, "on")
        assert lit(hass)

    async def test_switching_it_off_leaves_the_light_on(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, [{"trigger_entity": LOUNGE}])
        await set_state(hass, LOUNGE, "on")
        assert lit(hass)

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.kitchen_presence_detection"},
            blocking=True,
        )
        await set_state(hass, LOUNGE, "off")
        # Nobody's presence turned it on any more, so nobody's absence turns
        # it off: the room is as it was left.
        assert lit(hass)

    async def test_a_room_without_sensors_has_no_switch(
        self, hass: HomeAssistant
    ) -> None:
        await build(hass, [])
        assert hass.states.get("switch.kitchen_presence_detection") is None
