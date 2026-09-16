"""Switch presses, cycling, and manual-override detection."""

from __future__ import annotations

import datetime as dt

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.better_lighting.const import DOMAIN, SubentryType
from custom_components.better_lighting.render import ZoneMode
from tests.conftest import (
    MemberLight,
    add_zone_switch,
    hub_entry,
    setup_hub,
    setup_members,
    subentry_ids,
    zone_subentry,
)
from tests.ha.test_scenes import scene_subentry

ZONE = "light.kitchen"
SELECT = "select.kitchen_scenes"


def controller_subentry(
    name: str = "Oven switch",
    *,
    zone_id: str,
    scene_order: list[str],
    binding_entity: str | None = None,
    **overrides,
) -> ConfigSubentryData:
    data = {
        "name": name,
        "zone_id": zone_id,
        "binding_type": "entity_state" if binding_entity else "service_only",
        "binding_entity": binding_entity,
        "is_default": False,
        "scene_order": scene_order,
        "adaptive_position": "first",
        "off_at_end": False,
        "wrap_around": True,
        "on_foreign_state": "restart",
        "double_press_action": "cycle_previous",
        "long_press_action": "reset_adaptive",
        "press_states": ["on", "single"],
        "double_press_states": ["double"],
        "long_press_states": ["hold"],
        "press_attribute": "event_type",
        **overrides,
    }
    return ConfigSubentryData(
        data=data,
        subentry_type=SubentryType.CONTROLLER.value,
        title=name,
        unique_id=f"ctrl:{name.lower().replace(' ', '_')}",
    )


async def press_zone(hass: HomeAssistant) -> None:
    """A bare turn_on on the zone light: the plain-wall-switch path."""
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ZONE}, blocking=True
    )
    await hass.async_block_till_done()


class TestPlainSwitchCycling:
    """Requirement 1, with no controller configured at all."""

    async def test_first_press_turns_on_adaptive(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(
            hass, hub_entry(subentries_data=[zone_subentry(), scene_subentry("Cosy")])
        )

        await press_zone(hass)

        assert hass.states.get(SELECT).state == "Adaptive"
        assert hass.states.get("light.one").state == "on"

    async def test_further_presses_cycle_the_scenes(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(
            hass,
            hub_entry(
                subentries_data=[
                    zone_subentry(),
                    scene_subentry("Cosy"),
                    scene_subentry("Bright", brightness=100),
                ]
            ),
        )

        await press_zone(hass)
        assert hass.states.get(SELECT).state == "Adaptive"
        await press_zone(hass)
        assert hass.states.get(SELECT).state == "Cosy"
        await press_zone(hass)
        assert hass.states.get(SELECT).state == "Bright"

    async def test_the_cycle_wraps_back_to_adaptive(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(
            hass, hub_entry(subentries_data=[zone_subentry(), scene_subentry("Cosy")])
        )

        for _ in range(3):
            await press_zone(hass)
        assert hass.states.get(SELECT).state == "Adaptive"


class TestPerSwitchOrders:
    """Requirement 8: two switches in one room, different orders."""

    async def _setup(self, hass: HomeAssistant):
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        zone = zone_subentry()
        entry = hub_entry(
            subentries_data=[
                zone,
                scene_subentry("Cooking", brightness=100),
                scene_subentry("Dining", brightness=30),
            ]
        )
        await setup_hub(hass, entry)

        ids = subentry_ids(entry)
        zone_id = ids["Kitchen"]
        add_zone_switch(
            hass,
            entry,
            controller_subentry("Oven", zone_id=zone_id, scene_order=[ids["Cooking"]]),
            zone_id,
        )
        add_zone_switch(
            hass,
            entry,
            controller_subentry("Door", zone_id=zone_id, scene_order=[ids["Dining"]]),
            zone_id,
        )
        await hass.async_block_till_done()
        return entry

    async def test_each_switch_reaches_its_own_scene_first(
        self, hass: HomeAssistant
    ) -> None:
        await self._setup(hass)

        await hass.services.async_call(
            DOMAIN, "press", {"zone": "kitchen", "controller": "Oven"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

        await hass.services.async_call(
            DOMAIN, "press", {"zone": "kitchen", "controller": "Oven"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cooking"

    async def test_the_other_switch_restarts_its_own_list(
        self, hass: HomeAssistant
    ) -> None:
        await self._setup(hass)

        for _ in range(2):
            await hass.services.async_call(
                DOMAIN,
                "press",
                {"zone": "kitchen", "controller": "Oven"},
                blocking=True,
            )
            await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cooking"

        # Cooking is not on the door switch's list, so it starts from the top.
        await hass.services.async_call(
            DOMAIN, "press", {"zone": "kitchen", "controller": "Door"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

        await hass.services.async_call(
            DOMAIN, "press", {"zone": "kitchen", "controller": "Door"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Dining"


def _make(data: ConfigSubentryData):
    from homeassistant.config_entries import ConfigSubentry

    return ConfigSubentry(
        data=data["data"],
        subentry_type=data["subentry_type"],
        title=data["title"],
        unique_id=data["unique_id"],
    )


class TestEntityBinding:
    """A switch we watch ourselves."""

    async def _setup(self, hass: HomeAssistant, scenes=("Cosy",), **overrides):
        # Seed with a real, if stale, timestamp. Starting from "unknown" would
        # make the first fire an unknown -> value transition, which is
        # deliberately suppressed as a restart artefact.
        hass.states.async_set(
            "event.button",
            (dt_util.utcnow() - dt.timedelta(days=1)).isoformat(),
            {"event_type": "single"},
        )
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        zone = zone_subentry()
        entry = hub_entry(
            subentries_data=[zone, *(scene_subentry(name) for name in scenes)]
        )
        await setup_hub(hass, entry)
        ids = subentry_ids(entry)
        add_zone_switch(
            hass,
            entry,
            controller_subentry(
                "Wall",
                zone_id=ids["Kitchen"],
                scene_order=[ids[name] for name in scenes],
                binding_entity="event.button",
                **overrides,
            ),
            ids["Kitchen"],
        )
        await hass.async_block_till_done()
        return entry

    def _fire(self, hass: HomeAssistant, event_type: str = "single") -> None:
        hass.states.async_set(
            "event.button",
            dt_util.utcnow().isoformat(),
            {"event_type": event_type},
        )

    async def test_a_press_cycles_the_zone(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        # The room is dark, so the first press lights it in adaptive rather
        # than jumping straight into a scene.
        self._fire(hass)
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"
        assert hass.states.get("light.one").state == "on"

        self._fire(hass)
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cosy"

    async def test_the_same_value_twice_is_two_presses(
        self, hass: HomeAssistant
    ) -> None:
        """Pressing one button twice publishes the same value twice.

        That is not a state *change*, so a listener watching only for changes
        loses every second press. This is the most common way switch handling
        goes quietly wrong.
        """
        await self._setup(hass, scenes=("Cosy", "Bright"))
        fixed = dt_util.utcnow().isoformat()

        for _ in range(3):
            hass.states.async_set("event.button", fixed, {"event_type": "single"})
            await hass.async_block_till_done()

        # Three identical presses: on to adaptive, then Cosy, then Bright.
        assert hass.states.get(SELECT).state == "Bright"

    async def test_an_unknown_starting_state_is_not_a_press(
        self, hass: HomeAssistant
    ) -> None:
        """A restart leaves entities at unknown; that first write is not a press."""
        await self._setup(hass)
        hass.states.async_set("event.button", "unknown", {})
        await hass.async_block_till_done()
        self._fire(hass)
        await hass.async_block_till_done()
        # Only the unknown -> value transition happened, which is ignored.
        assert hass.states.get("light.one").state == "off"

    async def test_a_stale_timestamp_is_not_a_press(self, hass: HomeAssistant) -> None:
        """Some integrations restore an event entity's state across a restart."""
        await self._setup(hass)
        hass.states.async_set(
            "event.button",
            (dt_util.utcnow() - dt.timedelta(hours=2)).isoformat(),
            {"event_type": "single"},
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

    async def test_recovering_from_unavailable_is_not_a_press(
        self, hass: HomeAssistant
    ) -> None:
        await self._setup(hass)
        self._fire(hass)
        await hass.async_block_till_done()

        hass.states.async_set("event.button", "unavailable", {})
        await hass.async_block_till_done()
        self._fire(hass)
        await hass.async_block_till_done()
        # The unavailable -> value transition must not count, so only the
        # first press landed: still Adaptive rather than Cosy.
        assert hass.states.get(SELECT).state == "Adaptive"

    async def test_an_unknown_event_type_is_ignored(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        self._fire(hass, "brightness_move_up")
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

    async def test_long_press_returns_to_adaptive(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        self._fire(hass, "single")
        await hass.async_block_till_done()
        self._fire(hass, "single")
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cosy"

        self._fire(hass, "hold")
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

    async def test_rapid_taps_move_as_many_places(self, hass: HomeAssistant) -> None:
        """And each of them lands at once rather than waiting on a window."""
        await self._setup(hass, scenes=("Cosy", "Bright"))
        self._fire(hass)  # light the room, landing on adaptive
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

        for expected in ("Cosy", "Bright"):
            self._fire(hass)
            await hass.async_block_till_done()
            assert hass.states.get(SELECT).state == expected

    async def test_two_taps_stand_in_for_a_double_press(
        self, hass: HomeAssistant, freezer
    ) -> None:
        """Plenty of buttons have no double press and send the single twice."""
        await self._setup(
            hass,
            scenes=("Cosy", "Bright"),
            double_from_two_presses=True,
            double_press_window_ms=400,
        )
        self._fire(hass)  # light the room
        await hass.async_block_till_done()
        freezer.tick(dt.timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

        for _ in range(2):
            self._fire(hass)
            await hass.async_block_till_done()
        freezer.tick(dt.timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        # Backwards from adaptive, rather than two places forwards.
        assert hass.states.get(SELECT).state == "Bright"

    async def test_one_tap_is_still_one_tap_when_pairing(
        self, hass: HomeAssistant, freezer
    ) -> None:
        await self._setup(
            hass,
            scenes=("Cosy", "Bright"),
            double_from_two_presses=True,
            double_press_window_ms=400,
        )
        self._fire(hass)
        await hass.async_block_till_done()
        freezer.tick(dt.timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

        self._fire(hass)
        await hass.async_block_till_done()
        freezer.tick(dt.timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cosy"

    async def test_taps_further_apart_than_the_window_are_two_presses(
        self, hass: HomeAssistant, freezer
    ) -> None:
        """The window is the whole point of the setting: it has to bite."""
        await self._setup(
            hass,
            scenes=("Cosy", "Bright"),
            double_from_two_presses=True,
            double_press_window_ms=200,
        )
        for _ in range(3):
            self._fire(hass)
            await hass.async_block_till_done()
            freezer.tick(dt.timedelta(seconds=1))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()

        # On, then one place, then another: nothing was read as a double.
        assert hass.states.get(SELECT).state == "Bright"

    async def test_the_bus_hears_the_gesture_rather_than_the_taps(
        self, hass: HomeAssistant, freezer
    ) -> None:
        """An automation listening for a double press must hear one.

        With pairing on, what the button published is not what happened, and
        nobody can know which it was until the window closes -- so the event
        waits for the answer instead of reporting two presses that were not.
        """
        await self._setup(
            hass,
            scenes=("Cosy", "Bright"),
            double_from_two_presses=True,
            double_press_window_ms=400,
        )
        heard: list[str] = []
        hass.bus.async_listen(
            "better_lighting_press", lambda event: heard.append(event.data["kind"])
        )

        for _ in range(2):
            self._fire(hass)
            await hass.async_block_till_done()
        assert heard == []

        freezer.tick(dt.timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert heard == ["double_press"]

    async def test_a_press_is_acted_on_without_waiting(
        self, hass: HomeAssistant
    ) -> None:
        """No window, no debounce: a press that reports its own gesture is
        already unambiguous, and waiting on it only ever added lag."""
        await self._setup(hass, scenes=("Cosy", "Bright"))
        self._fire(hass)
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

        self._fire(hass)
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cosy"

    async def test_an_unknown_value_can_still_be_a_press(
        self, hass: HomeAssistant
    ) -> None:
        """A switch whose vocabulary we have none of -- a toggle alternating
        on and off is the common one -- cycles on anything it publishes."""
        await self._setup(hass, scenes=("Cosy", "Bright"), any_change_is_a_press=True)
        self._fire(hass, "on")
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

        # "off" is in no list this switch carries, and is a press all the same.
        self._fire(hass, "off")
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cosy"

    async def test_an_unknown_value_is_recorded_for_the_diagnostics(
        self, hass: HomeAssistant
    ) -> None:
        """The one thing the logs could not say: a value nobody recognises
        produces no press, and so leaves no trace of itself."""
        entry = await self._setup(hass, scenes=("Cosy",))
        self._fire(hass, "shake")
        await hass.async_block_till_done()

        runtimes = entry.runtime_data.switch_runtimes.values()
        seen = [item for runtime in runtimes for item in runtime.seen]
        assert {"shake"} == {item["value"] for item in seen}
        assert seen[0]["read_as"] == "nothing"


class TestToggleSwitches:
    """A switch wired straight to the zone's light entity that toggles.

    Its second press arrives as a turn-off, which read literally means the
    room can only alternate on and off -- never cycle.
    """

    async def _setup(self, hass: HomeAssistant, **overrides):
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            subentries_data=[
                zone_subentry(),
                scene_subentry("Cosy"),
                scene_subentry("Bright", brightness=100),
            ]
        )
        await setup_hub(hass, entry)
        ids = subentry_ids(entry)
        add_zone_switch(
            hass,
            entry,
            controller_subentry(
                "Wall plate",
                zone_id=ids["Kitchen"],
                scene_order=[ids["Cosy"], ids["Bright"]],
                is_default=True,
                binding_type="zone_light",
                **overrides,
            ),
            ids["Kitchen"],
        )
        await hass.async_block_till_done()
        return entry

    async def _toggle_off(self, hass: HomeAssistant) -> None:
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": ZONE}, blocking=True
        )
        await hass.async_block_till_done()

    async def test_a_turn_off_darkens_the_room_by_default(
        self, hass: HomeAssistant
    ) -> None:
        await self._setup(hass)
        await press_zone(hass)
        assert hass.states.get(SELECT).state == "Adaptive"

        await self._toggle_off(hass)
        assert hass.states.get("light.one").state == "off"

    async def test_a_toggle_cycles_instead_of_darkening(
        self, hass: HomeAssistant
    ) -> None:
        await self._setup(hass, any_change_is_a_press=True)
        await press_zone(hass)
        assert hass.states.get(SELECT).state == "Adaptive"

        await self._toggle_off(hass)
        assert hass.states.get(SELECT).state == "Cosy"
        assert hass.states.get("light.one").state == "on"

    async def test_a_dark_room_still_turns_off(self, hass: HomeAssistant) -> None:
        """Only a lit room reads a turn-off as the next press; otherwise the
        switch would light the room every time anything turned it off."""
        await self._setup(hass, any_change_is_a_press=True)
        await self._toggle_off(hass)
        assert hass.states.get("light.one").state == "off"


class TestServices:
    async def _setup(self, hass: HomeAssistant):
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(
            hass, hub_entry(subentries_data=[zone_subentry(), scene_subentry("Cosy")])
        )
        await press_zone(hass)

    async def test_activate_scene_by_name(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        await hass.services.async_call(
            DOMAIN,
            "activate_scene",
            {"zone": "kitchen", "scene": "Cosy"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cosy"

    async def test_set_adaptive_returns_from_a_scene(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        await hass.services.async_call(
            DOMAIN,
            "activate_scene",
            {"zone": "kitchen", "scene": "Cosy"},
            blocking=True,
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            DOMAIN, "set_adaptive", {"zone": "kitchen"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"

    async def test_cycle_service(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        await hass.services.async_call(
            DOMAIN, "cycle", {"zone": "kitchen"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cosy"

    async def test_targeting_by_entity(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        await hass.services.async_call(
            DOMAIN, "cycle", {"entity_id": ZONE}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cosy"

    async def test_an_unknown_zone_is_rejected(self, hass: HomeAssistant) -> None:
        await self._setup(hass)
        from homeassistant.exceptions import ServiceValidationError

        try:
            await hass.services.async_call(
                DOMAIN, "cycle", {"zone": "nowhere"}, blocking=True
            )
        except ServiceValidationError:
            return
        raise AssertionError("expected a ServiceValidationError")


class TestButtons:
    async def test_cycle_and_reset_buttons(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(
            hass, hub_entry(subentries_data=[zone_subentry(), scene_subentry("Cosy")])
        )
        await press_zone(hass)

        await hass.services.async_call(
            "button", "press", {"entity_id": "button.kitchen_next_scene"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Cosy"

        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.kitchen_back_to_adaptive"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Adaptive"


class TestManualOverride:
    async def _setup(self, hass: HomeAssistant):
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry(subentries_data=[zone_subentry()]))
        await press_zone(hass)
        return entry.runtime_data.controllers[next(iter(entry.runtime_data.zones))]

    async def test_a_foreign_change_marks_the_light_manual(
        self, hass: HomeAssistant
    ) -> None:
        controller = await self._setup(hass)
        assert not controller.manual

        # Somebody moves the bulb well away from what we asked for, under a
        # context that is not ours.
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.one", "brightness": 4},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()

        assert "light.one" in controller.manual

    async def test_a_small_drift_is_not_manual(self, hass: HomeAssistant) -> None:
        """Devices round their own brightness; that is not a person."""
        controller = await self._setup(hass)
        current = hass.states.get("light.one").attributes["brightness"]

        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.one", "brightness": current - 2},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()

        assert "light.one" not in controller.manual

    async def test_manual_is_per_light(self, hass: HomeAssistant) -> None:
        controller = await self._setup(hass)
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.one", "brightness": 4},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()

        assert "light.one" in controller.manual
        assert "light.two" not in controller.manual

    async def test_switching_a_light_off_clears_it(self, hass: HomeAssistant) -> None:
        controller = await self._setup(hass)
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.one", "brightness": 4},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()
        assert "light.one" in controller.manual

        await hass.services.async_call(
            "light", "turn_off", {"entity_id": "light.one"}, blocking=True
        )
        await hass.async_block_till_done()
        assert "light.one" not in controller.manual

    async def test_a_press_hands_control_back(self, hass: HomeAssistant) -> None:
        """The dismiss rule: the press lands on adaptive without advancing."""
        controller = await self._setup(hass)
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.one", "brightness": 4},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()
        assert controller.manual

        await press_zone(hass)

        assert not controller.manual
        assert hass.states.get(SELECT).state == "Adaptive"


class TestFollowingTheRoom:
    """The room can be changed without us. We should notice."""

    async def _setup(self, hass: HomeAssistant):
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry(subentries_data=[zone_subentry()]))
        await press_zone(hass)
        return entry.runtime_data.controllers[next(iter(entry.runtime_data.zones))]

    async def _switch_off(self, hass: HomeAssistant, *entity_ids: str) -> None:
        for entity_id in entity_ids:
            await hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": entity_id},
                blocking=True,
                context=Context(),
            )
            await hass.async_block_till_done()

    async def test_switching_every_light_off_by_hand_switches_the_room_off(
        self, hass: HomeAssistant
    ) -> None:
        controller = await self._setup(hass)
        assert controller.mode is ZoneMode.ADAPTIVE

        await self._switch_off(hass, "light.one", "light.two")

        assert hass.states.get(ZONE).state == "off"
        assert controller.mode is ZoneMode.OFF

    async def test_one_light_left_on_is_not_the_room_going_off(
        self, hass: HomeAssistant
    ) -> None:
        controller = await self._setup(hass)

        await self._switch_off(hass, "light.one")

        assert controller.mode is ZoneMode.ADAPTIVE

    async def test_the_next_press_starts_the_cycle_again(
        self, hass: HomeAssistant
    ) -> None:
        """The point of noticing: a press after a hand switch-off starts over."""
        controller = await self._setup(hass)
        await self._switch_off(hass, "light.one", "light.two")

        await press_zone(hass)

        assert controller.mode is ZoneMode.ADAPTIVE
        assert controller.active_scene_id is None

    async def test_lighting_one_by_hand_marks_the_room_on(
        self, hass: HomeAssistant
    ) -> None:
        controller = await self._setup(hass)
        await self._switch_off(hass, "light.one", "light.two")
        assert controller.mode is ZoneMode.OFF

        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.one"},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()

        assert controller.mode is not ZoneMode.OFF


class TestTwoButtonSwitches:
    """A rocker: up lights and cycles, down switches off, holds dim."""

    async def _setup(self, hass: HomeAssistant, **overrides):
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        hass.states.async_set("sensor.rocker", "idle")
        entry = hub_entry(subentries_data=[zone_subentry()])
        await setup_hub(hass, entry)
        ids = subentry_ids(entry)
        add_zone_switch(
            hass,
            entry,
            controller_subentry(
                "Rocker",
                zone_id=ids["Kitchen"],
                scene_order=[],
                binding_entity="sensor.rocker",
                press_attribute="",
                press_states=["up"],
                long_press_states=["up_hold"],
                long_press_action="brighten",
                dim_step_pct=10,
                **{
                    "down_press_states": ["down"],
                    "down_long_press_states": ["down_hold"],
                    **overrides,
                },
            ),
            ids["Kitchen"],
        )
        await hass.async_block_till_done()
        return entry.runtime_data.controllers[ids["Kitchen"]]

    async def _push(self, hass: HomeAssistant, value: str) -> None:
        hass.states.async_set("sensor.rocker", value)
        await hass.async_block_till_done()

    async def test_the_lower_half_switches_the_room_off(
        self, hass: HomeAssistant
    ) -> None:
        controller = await self._setup(hass)
        await self._push(hass, "up")
        assert controller.mode is ZoneMode.ADAPTIVE

        await self._push(hass, "down")

        assert controller.mode is ZoneMode.OFF

    async def test_each_half_pairs_its_own_taps(
        self, hass: HomeAssistant, freezer
    ) -> None:
        """A switch can report a double on one button and not the other, so
        the lower half decides for itself whether two taps mean one."""
        controller = await self._setup(
            hass,
            down_double_from_two_presses=True,
            down_double_press_window_ms=400,
            down_double_press_action="reset_adaptive",
        )
        await self._push(hass, "up")
        assert controller.mode is ZoneMode.ADAPTIVE

        # Two taps of the lower half, whose single press switches the room off.
        for _ in range(2):
            await self._push(hass, "down")
        freezer.tick(dt.timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        # Read as its double press instead, which is not "off".
        assert controller.mode is ZoneMode.ADAPTIVE

        # And the upper half, which said nothing about pairing, is unchanged.
        await self._push(hass, "idle")
        await self._push(hass, "down")
        freezer.tick(dt.timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert controller.mode is ZoneMode.OFF

    async def test_holding_down_dims_without_leaving_the_curve(
        self, hass: HomeAssistant
    ) -> None:
        controller = await self._setup(hass)
        await self._push(hass, "up")
        assert controller.bias_pct == 0

        await self._push(hass, "down_hold")

        assert controller.bias_pct == -10
        # Still adaptive: the room keeps tracking the sun, ten points below.
        assert controller.mode is ZoneMode.ADAPTIVE

    async def test_holding_up_brightens(self, hass: HomeAssistant) -> None:
        controller = await self._setup(hass)
        await self._push(hass, "up")

        await self._push(hass, "up_hold")

        assert controller.bias_pct == 10

    async def test_down_is_not_mistaken_for_an_ordinary_press(
        self, hass: HomeAssistant
    ) -> None:
        """ "off" is in both vocabularies, so the lower half is matched first."""
        controller = await self._setup(hass, down_press_states=["off"])
        await self._push(hass, "up")

        await self._push(hass, "off")

        assert controller.mode is ZoneMode.OFF


async def test_every_zone_button_is_available_without_hunting(
    hass: HomeAssistant,
) -> None:
    """Cycle back and clear-manual used to be off until you went looking."""
    await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
    await setup_hub(hass, hub_entry())

    for button in (
        "button.kitchen_next_scene",
        "button.kitchen_previous_scene",
        "button.kitchen_back_to_adaptive",
        "button.kitchen_clear_manual_override",
    ):
        assert hass.states.get(button) is not None, f"{button} is not enabled"
