"""What an installed system looks like after an update.

Nothing here adds a field; the point is that nothing has to. Every stored
object is read through a table of defaults, so a version that gains a setting
reads the versions that did not have it and answers the way it always did.
That only holds while every key is actually in its table, which is what these
check -- the failure mode otherwise is a KeyError on somebody else's house.
"""

from __future__ import annotations

import pathlib
import re

from homeassistant.config_entries import ConfigSubentry

from custom_components.better_lighting import models
from custom_components.better_lighting.const import SubentryType

COMPONENT = pathlib.Path(__file__).parents[2] / "custom_components" / "better_lighting"


def _subentry(kind: SubentryType, data: dict, name: str) -> ConfigSubentry:
    return ConfigSubentry(
        data=data,
        subentry_type=kind.value,
        title=name,
        unique_id=f"{kind.value}:{name.lower()}",
    )


class TestAnOlderInstallStillReads:
    """Each of these is the payload a much older version would have written."""

    def test_a_room_adapts_every_light_as_it_always_did(self) -> None:
        """The room-defined adaptive default has to be invisible to a house
        that has never heard of it, or an update would silently stop driving
        lights somebody depends on."""
        room = models.RoomConfig.from_subentry(
            _subentry(
                SubentryType.ROOM,
                {
                    "name": "Kitchen",
                    "lights": ["light.one", "light.two"],
                    "icon": "mdi:lightbulb-group",
                },
                "Kitchen",
            )
        )

        assert room.adaptive_lights == ()
        assert room.adapts("light.one")
        # Including a light added to the room after the update.
        assert room.adapts("light.added_later")

    def test_the_hub_gains_the_retention_default(self) -> None:
        assert models.HubConfig.from_options({}).log_retention_hours == 48

    def test_a_switch_keeps_working_and_gains_the_new_answers(self) -> None:
        switch = models.room_switch(
            {"switch_id": "one", "name": "Wall"}, "zone1", ["scene1", "scene2"]
        )

        assert switch.hold_ramp is True
        assert switch.release_states
        assert switch.any_change_is_a_press is False
        assert switch.double_from_two_presses is False
        # With no list of its own it cycles the room, as of v0.22 -- and
        # leads with adaptive, which is where a switch configured before
        # adaptive became an entry of its own said it went.
        assert switch.scene_order == ("__adaptive__", "scene1", "scene2")

    def test_a_mode_rule_without_scripts_reads(self) -> None:
        mode = models.ModeConfig.from_subentry(
            _subentry(
                SubentryType.MODE,
                {
                    "name": "Cinema",
                    "states": ["playing"],
                    "rules": [
                        {
                            "mode_states": ["playing"],
                            "zones": "zone1",
                            "action": "turn_off",
                        }
                    ],
                },
                "Cinema",
            )
        )

        assert mode.rules[0].scripts == ()
        assert mode.rules[0].rooms == frozenset({"zone1"})


def test_every_stored_key_has_a_default_behind_it() -> None:
    """A field read straight out of stored data, with no form behind it, is a
    KeyError on every house that upgrades into it.

    A key that some form writes is safe: it either carries a default, which
    the merge supplies, or it is required, which means the form that creates
    the object cannot leave it out. A key no form has ever heard of is
    neither, and nothing before the upgrade wrote it.

    Checked by reading the source rather than by exercising every path,
    because the paths that matter are the ones nobody thought to write a test
    for.
    """
    from custom_components.better_lighting import const

    source = (COMPONENT / "models.py").read_text()
    known = set()
    for table in (
        const.HUB_SPECS,
        const.ROOM_SPECS,
        const.LIGHT_PROFILE_SPECS,
        const.SCENE_SPECS,
        const.ROOM_SCENE_SPECS,
        const.CONTROLLER_SPECS,
        const.MODE_SPECS,
        const.COLOR_PRESET_SPECS,
        # Built per mode rather than held in a table, and merged the same way.
        const.mode_rule_specs([]),
        # The second step of the colour flow, which is a lookup rather than a
        # table because which field it asks for depends on the answer before.
        tuple(const.SCENE_COLOR_SPECS.values()),
    ):
        known |= {spec.key for spec in table}

    missing = []
    for name in set(re.findall(r"raw\[(CONF_\w+)\]", source)):
        key = getattr(const, name)
        if key not in known:
            missing.append(f"{name} ({key})")
    assert not missing, f"read from stored data with no default: {sorted(missing)}"


def test_a_room_written_before_zones_and_groups_reads() -> None:
    """Both are additive, which is why neither needs a migration.

    A room stored by any earlier version has no ``zones`` and no
    ``light_groups`` key. It must come back with none of either and behave
    exactly as it did -- no zones means one render unit, which is the shape
    the renderer had before zones existed at all.
    """
    room = models.RoomConfig.from_subentry(
        _subentry(
            SubentryType.ROOM,
            {"name": "Lounge", "lights": ["light.a", "light.b"]},
            "Lounge",
        )
    )

    assert room.zones == ()
    assert room.light_groups == ()
    assert room.group_tree().groups == {}

    from custom_components.better_lighting.zones import plan_units

    plans = plan_units(room.lights, room.zones)
    assert len(plans) == 1
    assert plans[0].lights == ("light.a", "light.b")
    assert plans[0].zone is None


def test_the_stored_vocabulary_still_says_zone() -> None:
    """Rooms were called zones once, and on disk they still are.

    A subentry's type cannot be changed after the fact: ``async_update_subentry``
    takes no ``subentry_type``, and remove-and-re-add runs
    ``async_clear_config_subentry`` over the device *and* entity registries,
    which would throw away every name, entity id and area the user set by hand.
    So the rename stopped at the identifiers, and these keys are frozen.

    Pinning them here because the next change introduces a real zone -- a part
    of a room -- and the temptation to tidy these up will be considerable.
    """
    from custom_components.better_lighting.const import (
        CONF_ROOM_ID,
        CONF_RULE_ROOMS,
        CONF_SCENE_ROOMS,
        SubentryType,
    )
    from custom_components.better_lighting.modes import EVENT_ROOM_OPTED_OUT
    from custom_components.better_lighting.room import EVENT_ROOM_MODE_CHANGED
    from custom_components.better_lighting.services import ATTR_ROOM_LEGACY

    assert SubentryType.ROOM.value == "zone"
    assert CONF_ROOM_ID == "zone_id"
    assert CONF_RULE_ROOMS == "zones"
    assert CONF_SCENE_ROOMS == "scene_zones"
    assert EVENT_ROOM_MODE_CHANGED == "better_lighting_zone_mode_changed"
    assert EVENT_ROOM_OPTED_OUT == "better_lighting_zone_opted_out"
    assert ATTR_ROOM_LEGACY == "zone"
