"""Restarts, reloads, broken references, and diagnostics."""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.better_lighting.const import DOMAIN
from custom_components.better_lighting.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.conftest import (
    MemberLight,
    hub_entry,
    setup_hub,
    setup_members,
    zone_subentry,
)
from tests.ha.test_modes import _sub, build, mode_subentry, rule, set_state
from tests.ha.test_scenes import scene_subentry

CINEMA = "select.home_cinema_state"


class TestSessionSurvivesAReload:
    async def test_the_snapshot_is_not_lost(self, hass: HomeAssistant) -> None:
        """The gap this milestone exists to close.

        A restart mid-film used to lose the snapshot, so the ending would
        relight every light rather than only the ones that were on.
        """
        entry, _ids = await build(hass)
        await set_state(hass, "playing")
        session_id = hass.states.get(CINEMA).attributes["bl_session_id"]
        taken_at = hass.states.get(CINEMA).attributes["bl_snapshot_taken_at"]
        assert hass.states.get("light.kitchen_main").state == "off"

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        # Same session, same snapshot: the film did not restart.
        assert hass.states.get(CINEMA).state == "playing"
        assert hass.states.get(CINEMA).attributes["bl_session_id"] == session_id
        assert hass.states.get(CINEMA).attributes["bl_snapshot_taken_at"] == taken_at

    async def test_the_ending_still_relights_only_what_was_on(
        self, hass: HomeAssistant
    ) -> None:
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
        ids = {sub.title: sub.subentry_id for sub in entry.subentries.values()}
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
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        await set_state(hass, "off")

        assert hass.states.get("light.lounge_main").state == "on"
        # Off before the film, so still off after a restart and the ending.
        assert hass.states.get("light.lounge_lamp").state == "off"
        assert hass.states.get("light.kitchen_main").state == "on"

    async def test_opting_out_survives_too(self, hass: HomeAssistant) -> None:
        """Losing this would yank a room back under the film's control."""
        entry, _ids = await build(hass)
        await set_state(hass, "playing")
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(CINEMA).attributes["bl_opted_out"]

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get(CINEMA).attributes["bl_opted_out"]

    async def test_a_finished_session_does_not_come_back(
        self, hass: HomeAssistant
    ) -> None:
        entry, _ids = await build(hass)
        await set_state(hass, "playing")
        await set_state(hass, "off")

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get(CINEMA).state == "off"
        assert hass.states.get(CINEMA).attributes["bl_session_id"] is None

    async def test_restoring_does_not_replay_commands(
        self, hass: HomeAssistant
    ) -> None:
        """Reconcile, not replay: rooms already correct receive nothing."""
        entry, _ids = await build(hass)
        await set_state(hass, "playing")
        lounge_before = hass.states.get("light.lounge_main").attributes["brightness"]

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert (
            hass.states.get("light.lounge_main").attributes["brightness"]
            == lounge_before
        )
        assert hass.states.get("light.kitchen_main").state == "off"


class TestDanglingReferences:
    async def test_a_deleted_scene_raises_a_repair_issue(
        self, hass: HomeAssistant
    ) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(subentries_data=[zone_subentry(), scene_subentry("Cosy")])
        await setup_hub(hass, entry)
        scene_id = next(
            sub.subentry_id for sub in entry.subentries.values() if sub.title == "Cosy"
        )
        zone = next(sub for sub in entry.subentries.values() if sub.title == "Kitchen")
        hass.config_entries.async_update_subentry(
            entry, zone, data={**zone.data, "night_scene_id": scene_id}
        )
        await hass.async_block_till_done()
        assert not _our_issues(hass)

        hass.config_entries.async_remove_subentry(entry, scene_id)
        await hass.async_block_till_done()

        issues = _our_issues(hass)
        assert any("missing_scene" in issue for issue in issues), issues

    async def test_the_issue_clears_once_fixed(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(subentries_data=[zone_subentry(), scene_subentry("Cosy")])
        await setup_hub(hass, entry)
        scene_id = next(
            sub.subentry_id for sub in entry.subentries.values() if sub.title == "Cosy"
        )
        zone = next(sub for sub in entry.subentries.values() if sub.title == "Kitchen")
        hass.config_entries.async_update_subentry(
            entry, zone, data={**zone.data, "insect_scene_id": scene_id}
        )
        await hass.async_block_till_done()
        hass.config_entries.async_remove_subentry(entry, scene_id)
        await hass.async_block_till_done()
        assert _our_issues(hass)

        zone = next(sub for sub in entry.subentries.values() if sub.title == "Kitchen")
        hass.config_entries.async_update_subentry(
            entry, zone, data={**zone.data, "insect_scene_id": None}
        )
        await hass.async_block_till_done()

        assert not _our_issues(hass)

    async def test_a_deleted_scene_only_shortens_a_cycle(
        self, hass: HomeAssistant
    ) -> None:
        """Lenient at runtime: a missing step is skipped, not crashed on."""
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(subentries_data=[zone_subentry(), scene_subentry("Cosy")])
        await setup_hub(hass, entry)
        scene_id = next(
            sub.subentry_id for sub in entry.subentries.values() if sub.title == "Cosy"
        )
        hass.config_entries.async_remove_subentry(entry, scene_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get("select.kitchen_scenes").state == "Adaptive"


def _our_issues(hass: HomeAssistant) -> list[str]:
    return [
        issue.issue_id
        for issue in ir.async_get(hass).issues.values()
        if issue.domain == DOMAIN
    ]


class TestSerialisation:
    async def test_concurrent_changes_do_not_interleave(
        self, hass: HomeAssistant
    ) -> None:
        """A press, presence and a tick can all arrive at once."""
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(
            subentries_data=[
                zone_subentry(),
                scene_subentry("Cosy"),
                scene_subentry("Bright", brightness=100),
            ]
        )
        await setup_hub(hass, entry)
        controller = next(iter(entry.runtime_data.controllers.values()))

        await asyncio.gather(
            *(
                hass.services.async_call(
                    "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
                )
                for _ in range(6)
            )
        )
        await hass.async_block_till_done()

        # Whatever order they landed in, the room ends in exactly one mode.
        select = hass.states.get("select.kitchen_scenes")
        assert select.state in select.attributes["options"]
        assert controller.mode.value in ("adaptive", "scene", "off")


class TestDiagnostics:
    async def test_dump_includes_config_and_live_state(
        self, hass: HomeAssistant
    ) -> None:
        entry, _ids = await build(hass)
        await set_state(hass, "playing")

        data = await async_get_config_entry_diagnostics(hass, entry)

        assert data["hub"]["interval"] == 90
        assert len(data["zones"]) == 2
        assert data["scenes"]
        mode = next(iter(data["modes"].values()))
        assert mode["state"] == "playing"
        assert mode["session_id"] is not None
        assert mode["snapshot_previously_on"]

    async def test_dump_surfaces_manual_overrides(self, hass: HomeAssistant) -> None:
        from homeassistant.core import Context

        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(subentries_data=[zone_subentry()])
        await setup_hub(hass, entry)
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
        )
        await hass.async_block_till_done()
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.one", "brightness": 4},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()

        data = await async_get_config_entry_diagnostics(hass, entry)
        zone = next(iter(data["zones"].values()))
        # The first question a surprising-behaviour report needs answered.
        assert "light.one" in zone["manual"]
