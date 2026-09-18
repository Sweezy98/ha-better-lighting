"""Room light entity: aggregation, relative dimming, and on-state memory."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from tests.conftest import (
    MemberLight,
    hub_entry,
    room_subentry,
    setup_hub,
    setup_members,
)

ROOM = "light.kitchen"


async def _setup(
    hass: HomeAssistant, one: MemberLight, two: MemberLight, **room_kwargs
):
    await setup_members(hass, [one, two])
    entry = hub_entry(**room_kwargs) if room_kwargs else hub_entry()
    await setup_hub(hass, entry)
    return entry


async def _call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "light", service, {"entity_id": ROOM, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def test_creates_one_entity_per_room(hass: HomeAssistant) -> None:
    await _setup(
        hass,
        MemberLight("One", is_on=True, brightness=100),
        MemberLight("Two", is_on=True, brightness=100),
    )
    state = hass.states.get(ROOM)
    assert state is not None
    assert state.state == "on"
    assert state.attributes["entity_id"] == ["light.one", "light.two"]


async def test_brightness_averages_only_lit_members(hass: HomeAssistant) -> None:
    await _setup(
        hass,
        MemberLight("One", is_on=True, brightness=200),
        MemberLight("Two", is_on=False),
    )
    # The dark member contributes nothing; averaging in a zero would report 100.
    assert hass.states.get(ROOM).attributes["brightness"] == 200


async def test_any_member_on_means_the_room_is_on(hass: HomeAssistant) -> None:
    await _setup(
        hass,
        MemberLight("One", is_on=True, brightness=100),
        MemberLight("Two", is_on=False),
    )
    assert hass.states.get(ROOM).state == "on"


async def test_room_reports_member_colour_capabilities(hass: HomeAssistant) -> None:
    await _setup(
        hass,
        MemberLight("One", is_on=True, brightness=100),
        MemberLight("Two", is_on=True, brightness=100),
    )
    modes = hass.states.get(ROOM).attributes["supported_color_modes"]
    # Refined from the ONOFF placeholder seeded before registration.
    assert set(modes) == {"color_temp", "hs"}


async def test_turn_off_switches_every_member_off(hass: HomeAssistant) -> None:
    one = MemberLight("One", is_on=True, brightness=100)
    two = MemberLight("Two", is_on=True, brightness=100)
    await _setup(hass, one, two)

    await _call(hass, "turn_off")

    assert hass.states.get("light.one").state == "off"
    assert hass.states.get("light.two").state == "off"
    assert hass.states.get(ROOM).state == "off"


async def test_only_previously_on_lights_come_back(hass: HomeAssistant) -> None:
    """The headline on-state memory behaviour."""
    one = MemberLight("One", is_on=True, brightness=180)
    two = MemberLight("Two", is_on=False)
    await _setup(hass, one, two)

    await _call(hass, "turn_off")
    assert hass.states.get(ROOM).state == "off"

    await _call(hass, "turn_on")

    assert hass.states.get("light.one").state == "on"
    # light.two was already off before the room was switched off, so it stays off.
    assert hass.states.get("light.two").state == "off"


async def test_all_members_light_when_nothing_was_remembered(
    hass: HomeAssistant,
) -> None:
    await _setup(hass, MemberLight("One"), MemberLight("Two"))

    await _call(hass, "turn_on")

    assert hass.states.get("light.one").state == "on"
    assert hass.states.get("light.two").state == "on"


async def test_relative_dim_moves_members_by_their_own_headroom(
    hass: HomeAssistant,
) -> None:
    """Dimming the group must not flatten the members to a single value."""
    one = MemberLight("One", is_on=True, brightness=50)
    two = MemberLight("Two", is_on=True, brightness=200)
    await _setup(hass, one, two)
    assert hass.states.get(ROOM).attributes["brightness"] == 125

    await _call(hass, "turn_on", brightness=62)

    # Each roughly halved, rather than both landing on 62.
    assert hass.states.get("light.one").attributes["brightness"] == pytest.approx(
        25, abs=2
    )
    assert hass.states.get("light.two").attributes["brightness"] == pytest.approx(
        99, abs=2
    )


async def test_relative_dim_preserves_ordering(hass: HomeAssistant) -> None:
    one = MemberLight("One", is_on=True, brightness=40)
    two = MemberLight("Two", is_on=True, brightness=220)
    await _setup(hass, one, two)

    await _call(hass, "turn_on", brightness=200)

    dim = hass.states.get("light.one").attributes["brightness"]
    bright = hass.states.get("light.two").attributes["brightness"]
    assert dim < bright, "the dimmer light must stay the dimmer light"


async def test_a_colour_does_not_light_dark_members(hass: HomeAssistant) -> None:
    """Changing a room's colour adjusts the light that is there.

    Home Assistant's own light group would forward to every member, but a room
    lighting itself up because you touched the colour wheel is a worse
    surprise than one lamp staying dark. Configurable per room.
    """
    one = MemberLight("One", is_on=True, brightness=100)
    two = MemberLight("Two", is_on=False)
    await _setup(hass, one, two)

    await _call(hass, "turn_on", color_temp_kelvin=4200)

    assert hass.states.get("light.one").attributes["color_temp_kelvin"] == 4200
    assert hass.states.get("light.two").state == "off"


async def test_dark_members_can_be_opted_in(hass: HomeAssistant) -> None:
    one = MemberLight("One", is_on=True, brightness=100)
    two = MemberLight("Two", is_on=False)
    await setup_members(hass, [one, two])
    await setup_hub(
        hass, hub_entry(subentries_data=[room_subentry(color_lights_dark_members=True)])
    )

    await _call(hass, "turn_on", color_temp_kelvin=4200)

    for entity_id in ("light.one", "light.two"):
        state = hass.states.get(entity_id)
        assert state.state == "on"
        assert state.attributes["color_temp_kelvin"] == 4200


async def test_dimming_still_does_not_light_dark_members(
    hass: HomeAssistant,
) -> None:
    """The one case that is genuinely different.

    Dimming is a relative adjustment of what is lit; applying it to a dark
    lamp would mean switching it on in order to make it dimmer.
    """
    one = MemberLight("One", is_on=True, brightness=200)
    two = MemberLight("Two", is_on=False)
    await _setup(hass, one, two)

    await _call(hass, "turn_on", brightness=80)

    assert hass.states.get("light.one").attributes["brightness"] < 200
    assert hass.states.get("light.two").state == "off"
