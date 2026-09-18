"""Zones, end to end: the desk that does not go dark for the film.

One living room, two places in it. The couch and the desk are lit together,
switched together and adapt together -- until the film starts and somebody is
working at the desk, at which point the desk carries on being a desk and goes
back to the room when they leave.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from tests.conftest import (
    MemberLight,
    hub_entry,
    room_subentry,
    setup_hub,
    setup_members,
    subentry_ids,
)
from tests.ha.test_modes import _sub, mode_subentry, rule, set_state
from tests.ha.test_scenes import scene_subentry

DESK_SENSOR = "binary_sensor.desk"
LIGHTS = ["light.couch", "light.desk"]

DESK_ZONE = {
    "zone_id": "desk",
    "name": "Desk",
    "lights": ["light.desk"],
    "presence_entity": DESK_SENSOR,
    "presence_clear_delay": 0,
    "detach_on_mode": True,
}


async def _build(hass: HomeAssistant, *, zones=(DESK_ZONE,), occupied: bool = False):
    """A living room of a couch and a desk, and a film over the whole house."""
    await setup_members(
        hass,
        [
            MemberLight("Couch", is_on=True, brightness=200),
            MemberLight("Desk", is_on=True, brightness=200),
        ],
    )
    hass.states.async_set(DESK_SENSOR, "on" if occupied else "off")

    entry = hub_entry(
        subentries_data=[
            room_subentry("Living room", LIGHTS, zones=list(zones)),
            scene_subentry("Movie", brightness=5),
        ]
    )
    await setup_hub(hass, entry)

    ids = subentry_ids(entry)
    rules = [
        rule(["playing"], [ids["Living room"]], "apply_scene", scene_id=ids["Movie"])
    ]
    hass.config_entries.async_add_subentry(entry, _sub(mode_subentry(rules=rules)))
    await hass.async_block_till_done()
    return entry, ids


def _controller(entry):
    return next(iter(entry.runtime_data.controllers.values()))


def _brightness(hass: HomeAssistant, entity_id: str) -> int | None:
    state = hass.states.get(entity_id)
    return state.attributes.get("brightness") if state else None


class TestARoomWithoutZones:
    async def test_it_behaves_exactly_as_before(self, hass: HomeAssistant) -> None:
        """The case that has to stay unchanged, since most rooms are this one."""
        entry, _ids = await _build(hass, zones=())
        await set_state(hass, "playing")

        # Both lights went to the film scene; nothing stood apart.
        assert _brightness(hass, "light.couch") == _brightness(hass, "light.desk")
        assert _controller(entry).detached_zones == frozenset()


class TestAZoneThatIsFollowingTheRoom:
    async def test_an_empty_desk_goes_dark_with_the_room(
        self, hass: HomeAssistant
    ) -> None:
        """A zone is part of its room until it has a reason not to be."""
        entry, _ids = await _build(hass)
        await set_state(hass, "playing")

        assert _controller(entry).detached_zones == frozenset()
        assert _brightness(hass, "light.desk") == _brightness(hass, "light.couch")

    async def test_occupancy_alone_detaches_nothing(self, hass: HomeAssistant) -> None:
        """With no film on, a busy desk is the room's own business."""
        entry, _ids = await _build(hass)

        hass.states.async_set(DESK_SENSOR, "on")
        await hass.async_block_till_done()

        assert _controller(entry).detached_zones == frozenset()


class TestTheDeskDuringAFilm:
    async def test_a_desk_occupied_first_is_never_darkened(
        self, hass: HomeAssistant
    ) -> None:
        """Better than darkening it and putting it back a moment later."""
        entry, _ids = await _build(hass, occupied=True)

        await set_state(hass, "playing")

        assert _controller(entry).detached_zones == frozenset({"desk"})
        assert _brightness(hass, "light.desk") > _brightness(hass, "light.couch")

    async def test_sitting_down_mid_film_takes_the_desk_out(
        self, hass: HomeAssistant
    ) -> None:
        entry, _ids = await _build(hass)
        await set_state(hass, "playing")
        dark = _brightness(hass, "light.desk")

        hass.states.async_set(DESK_SENSOR, "on")
        await hass.async_block_till_done()

        assert _controller(entry).detached_zones == frozenset({"desk"})
        assert _brightness(hass, "light.desk") > dark

    async def test_the_rest_of_the_room_stays_in_the_film(
        self, hass: HomeAssistant
    ) -> None:
        _entry, _ids = await _build(hass, occupied=True)
        await set_state(hass, "playing")

        couch = _brightness(hass, "light.couch")
        assert couch is not None
        # The film scene is 5%, so the couch is dim and the desk is not.
        assert couch < 40

    async def test_leaving_puts_the_desk_back_in_the_film(
        self, hass: HomeAssistant
    ) -> None:
        """Rejoining is the default, so nothing has to be put back by hand."""
        entry, _ids = await _build(hass, occupied=True)
        await set_state(hass, "playing")
        assert _controller(entry).detached_zones == frozenset({"desk"})

        hass.states.async_set(DESK_SENSOR, "off")
        await hass.async_block_till_done()

        assert _controller(entry).detached_zones == frozenset()
        assert _brightness(hass, "light.desk") == _brightness(hass, "light.couch")

    async def test_the_film_ending_brings_every_zone_back(
        self, hass: HomeAssistant
    ) -> None:
        entry, _ids = await _build(hass, occupied=True)
        await set_state(hass, "playing")

        await set_state(hass, "off")

        assert _controller(entry).detached_zones == frozenset()

    async def test_a_zone_that_was_not_asked_to_detach_stays_put(
        self, hass: HomeAssistant
    ) -> None:
        """Detaching is opt-in: a zone that merely exists changes nothing."""
        entry, _ids = await _build(
            hass, zones=({**DESK_ZONE, "detach_on_mode": False},), occupied=True
        )

        await set_state(hass, "playing")

        assert _controller(entry).detached_zones == frozenset()
        assert _brightness(hass, "light.desk") == _brightness(hass, "light.couch")


class TestWhatADetachedZoneDoes:
    async def test_it_follows_its_own_scene_when_it_names_one(
        self, hass: HomeAssistant
    ) -> None:
        await setup_members(
            hass,
            [
                MemberLight("Couch", is_on=True, brightness=200),
                MemberLight("Desk", is_on=True, brightness=200),
            ],
        )
        hass.states.async_set(DESK_SENSOR, "on")
        entry = hub_entry(
            subentries_data=[
                room_subentry(
                    "Living room",
                    LIGHTS,
                    scenes=[
                        {
                            "scene_id": "work",
                            "name": "Work",
                            "lights": {
                                "light.desk": {"action": "apply", "brightness_pct": 90}
                            },
                        }
                    ],
                    zones=[{**DESK_ZONE, "detached_scene_id": "work"}],
                ),
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
                            [ids["Living room"]],
                            "apply_scene",
                            scene_id=ids["Movie"],
                        )
                    ]
                )
            ),
        )
        await hass.async_block_till_done()

        await set_state(hass, "playing")

        assert _controller(entry).detached_zones == frozenset({"desk"})
        assert _brightness(hass, "light.desk") > 200
