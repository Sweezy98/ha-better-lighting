"""Light groups, end to end: one command on the wire instead of three.

The pure tests in ``tests/pure/test_groups.py`` cover the shape of a group and
the rules for using one. These cover the thing that is actually the point --
that a room with a group sends to the group entity, and stops the moment its
bulbs are meant to differ.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.better_lighting.render import Trigger
from tests.conftest import (
    MemberLight,
    hub_entry,
    room_subentry,
    setup_hub,
    setup_members,
)

ROOM = "light.kitchen"
CEILING = "light.ceiling"
BULBS = ["light.one", "light.two", "light.three"]

GROUP = {
    "group_id": "g1",
    "name": "Ceiling",
    "lights": BULBS,
    "send_entity": CEILING,
}


async def _setup(hass: HomeAssistant, *, groups=(GROUP,), scenes=()):
    """A room of three bulbs, plus the group entity that addresses them."""
    await setup_members(
        hass,
        [
            MemberLight("One", is_on=True, brightness=100),
            MemberLight("Two", is_on=True, brightness=100),
            MemberLight("Three", is_on=True, brightness=100),
            MemberLight("Ceiling", is_on=True, brightness=100),
        ],
    )
    entry = hub_entry(
        subentries_data=[
            room_subentry(
                lights=BULBS,
                light_groups=list(groups),
                scenes=list(scenes),
            )
        ]
    )
    await setup_hub(hass, entry)
    return entry


def _rgb(red: int, green: int, blue: int) -> dict:
    """One light's entry in a scene, holding a colour."""
    return {
        "action": "apply",
        "color_format": "rgb_color",
        "rgb_color": [red, green, blue],
    }


def _calls(service_calls, service: str = "turn_on"):
    """Light calls we made, as (entity ids, payload)."""
    out = []
    for call in service_calls:
        if call.domain != "light" or call.service != service:
            continue
        target = call.data.get("entity_id")
        out.append(
            (
                sorted(target) if isinstance(target, list) else [target],
                {k: v for k, v in call.data.items() if k != "entity_id"},
            )
        )
    return out


async def test_the_group_entity_carries_the_whole_room(
    hass: HomeAssistant, service_calls
) -> None:
    """Three entities is three Zigbee commands; the group is one multicast."""
    entry = await _setup(hass)
    controller = next(iter(entry.runtime_data.controllers.values()))
    service_calls.clear()

    await controller.async_render(Trigger.ACTIVATE)
    await hass.async_block_till_done()

    targets = [entities for entities, _ in _calls(service_calls)]
    assert targets == [[CEILING]]


async def test_a_scene_that_differs_addresses_the_bulbs(
    hass: HomeAssistant, service_calls
) -> None:
    """One red, one green, one blue is the reason per-bulb control exists."""
    scene = {
        "scene_id": "fun",
        "name": "Fun",
        "lights": {
            "light.one": _rgb(255, 0, 0),
            "light.two": _rgb(0, 255, 0),
            "light.three": _rgb(0, 0, 255),
        },
    }
    entry = await _setup(hass, scenes=(scene,))
    controller = next(iter(entry.runtime_data.controllers.values()))
    service_calls.clear()

    await controller.async_activate_scene("fun")
    await hass.async_block_till_done()

    targets = sorted(e for entities, _ in _calls(service_calls) for e in entities)
    assert CEILING not in targets
    assert targets == sorted(BULBS)


async def test_a_scene_can_speak_to_the_group_by_name(
    hass: HomeAssistant, service_calls
) -> None:
    """The whole point of naming a bundle: one row in the scene, not three."""
    scene = {
        "scene_id": "dim",
        "name": "Dim",
        "groups": {"g1": {"action": "apply", "brightness_pct": 20}},
    }
    entry = await _setup(hass, scenes=(scene,))
    controller = next(iter(entry.runtime_data.controllers.values()))
    service_calls.clear()

    await controller.async_activate_scene("dim")
    await hass.async_block_till_done()

    calls = _calls(service_calls)
    assert [entities for entities, _ in calls] == [[CEILING]]
    assert calls[0][1]["brightness"] == pytest.approx(51, abs=2)


async def test_one_bulb_overriding_the_group_breaks_it_up(
    hass: HomeAssistant, service_calls
) -> None:
    scene = {
        "scene_id": "mostly",
        "name": "Mostly",
        "groups": {"g1": {"action": "apply", "brightness_pct": 20}},
        "lights": {"light.three": {"action": "off"}},
    }
    entry = await _setup(hass, scenes=(scene,))
    controller = next(iter(entry.runtime_data.controllers.values()))
    service_calls.clear()

    await controller.async_activate_scene("mostly")
    await hass.async_block_till_done()

    on_targets = sorted(e for entities, _ in _calls(service_calls) for e in entities)
    off_targets = sorted(
        e for entities, _ in _calls(service_calls, "turn_off") for e in entities
    )
    assert on_targets == ["light.one", "light.two"]
    assert off_targets == ["light.three"]


async def test_a_group_without_a_send_entity_changes_nothing(
    hass: HomeAssistant, service_calls
) -> None:
    entry = await _setup(hass, groups=({**GROUP, "send_entity": None},))
    controller = next(iter(entry.runtime_data.controllers.values()))
    service_calls.clear()

    await controller.async_render(Trigger.ACTIVATE)
    await hass.async_block_till_done()

    targets = sorted(e for entities, _ in _calls(service_calls) for e in entities)
    assert targets == sorted(BULBS)


async def test_a_stored_cycle_leaves_the_room_working(hass: HomeAssistant) -> None:
    """Hand-edited storage should cost the groups, not the room."""
    entry = await _setup(
        hass,
        groups=(
            {"group_id": "a", "name": "A", "groups": ["b"], "lights": ["light.one"]},
            {"group_id": "b", "name": "B", "groups": ["a"], "lights": ["light.two"]},
        ),
    )
    controller = next(iter(entry.runtime_data.controllers.values()))

    assert controller.groups.groups == {}
    assert hass.states.get(ROOM) is not None
