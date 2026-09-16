"""Restarts, reloads, broken references, and diagnostics."""

from __future__ import annotations

import asyncio
import pathlib

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.better_lighting.const import DOMAIN
from custom_components.better_lighting.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.conftest import (
    MemberLight,
    hub_entry,
    remove_zone_scene,
    setup_hub,
    setup_members,
    subentry_ids,
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
        scene_id = subentry_ids(entry)["Cosy"]
        zone = next(sub for sub in entry.subentries.values() if sub.title == "Kitchen")
        hass.config_entries.async_update_subentry(
            entry, zone, data={**zone.data, "night_scene_id": scene_id}
        )
        await hass.async_block_till_done()
        assert not _our_issues(hass)

        remove_zone_scene(hass, entry, scene_id)
        await hass.async_block_till_done()

        issues = _our_issues(hass)
        assert any("missing_scene" in issue for issue in issues), issues

    async def test_the_issue_clears_once_fixed(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = hub_entry(subentries_data=[zone_subentry(), scene_subentry("Cosy")])
        await setup_hub(hass, entry)
        scene_id = subentry_ids(entry)["Cosy"]
        zone = next(sub for sub in entry.subentries.values() if sub.title == "Kitchen")
        hass.config_entries.async_update_subentry(
            entry, zone, data={**zone.data, "insect_scene_id": scene_id}
        )
        await hass.async_block_till_done()
        remove_zone_scene(hass, entry, scene_id)
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
        scene_id = subentry_ids(entry)["Cosy"]
        remove_zone_scene(hass, entry, scene_id)
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


async def test_a_device_with_no_owner_is_swept_up(hass: HomeAssistant) -> None:
    """Switches used to own a device each; deleting one left it behind."""
    await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
    entry = await setup_hub(hass, hub_entry())

    registry = dr.async_get(hass)
    orphan = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "a-switch-that-was-deleted")},
        name="Old switch",
    )
    assert entry.entry_id in orphan.config_entries

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # Ours was its only entry, so the registry drops the device altogether.
    assert registry.async_get(orphan.id) is None


class TestNothingIsLeftBehind:
    """What each kind of removal has to take with it."""

    async def _with_a_switch(self, hass: HomeAssistant):
        from tests.conftest import add_zone_switch
        from tests.ha.test_controllers import controller_subentry

        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry())
        ids = subentry_ids(entry)
        switch_id = add_zone_switch(
            hass,
            entry,
            controller_subentry("Door", zone_id=ids["Kitchen"], scene_order=[]),
            ids["Kitchen"],
        )
        await hass.async_block_till_done()
        return entry, ids["Kitchen"], switch_id

    async def test_removing_a_switch_removes_its_entity(
        self, hass: HomeAssistant
    ) -> None:
        """A switch lives inside a room, so no subentry removal cleans up."""
        entry, zone_id, switch_id = await self._with_a_switch(hass)
        registry = er.async_get(hass)
        assert any(
            switch_id in (e.unique_id or "")
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        )

        zone = entry.subentries[zone_id]
        hass.config_entries.async_update_subentry(
            entry, zone, data={**zone.data, "switches": []}
        )
        await hass.async_block_till_done()

        assert not any(
            switch_id in (e.unique_id or "")
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        )

    async def test_removing_a_room_removes_its_entities(
        self, hass: HomeAssistant
    ) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry())
        zone_id = subentry_ids(entry)["Kitchen"]
        assert hass.states.get("light.kitchen") is not None

        hass.config_entries.async_remove_subentry(entry, zone_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        assert not [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.unique_id.startswith(zone_id)
        ]

    async def test_a_deleted_modes_stored_session_is_forgotten(
        self, hass: HomeAssistant
    ) -> None:
        """It would otherwise sit in the file and restore into nothing."""
        from custom_components.better_lighting.store import PersistedSession

        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry())
        sessions = entry.runtime_data.sessions
        sessions.put(
            PersistedSession(
                mode_id="a-mode-that-was-deleted", session_id="x", state="playing"
            )
        )

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.runtime_data.sessions.get("a-mode-that-was-deleted") is None

    async def test_removing_the_integration_takes_its_repair_issues(
        self, hass: HomeAssistant
    ) -> None:
        from custom_components.better_lighting import async_remove_entry

        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry())
        ir.async_create_issue(
            hass,
            DOMAIN,
            "leftover",
            is_fixable=False,
            severity="warning",
            translation_key="missing_scene",
        )
        assert _our_issues(hass)

        await hass.config_entries.async_unload(entry.entry_id)
        await async_remove_entry(hass, entry)

        assert not _our_issues(hass)

    async def test_removing_the_integration_takes_its_session_file(
        self, hass: HomeAssistant
    ) -> None:
        from custom_components.better_lighting import async_remove_entry
        from custom_components.better_lighting.store import (
            PersistedSession,
            SessionStore,
        )

        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry())
        entry.runtime_data.sessions.put(
            PersistedSession(mode_id="m", session_id="s", state="playing")
        )
        await entry.runtime_data.sessions.async_flush()

        await hass.config_entries.async_unload(entry.entry_id)
        await async_remove_entry(hass, entry)

        fresh = SessionStore(hass)
        await fresh.async_load()
        assert fresh.get("m") is None

    async def test_removing_the_integration_takes_its_services(
        self, hass: HomeAssistant
    ) -> None:
        """They hang off the domain, so nothing else would remove them."""
        from custom_components.better_lighting import async_remove_entry

        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry())
        assert hass.services.has_service(DOMAIN, "press")

        await hass.config_entries.async_unload(entry.entry_id)
        await async_remove_entry(hass, entry)

        assert not hass.services.async_services().get(DOMAIN)

    async def test_every_declared_service_is_removed(self) -> None:
        """A service added later must be added to the teardown too."""
        import re

        source = (
            pathlib.Path(__file__).parents[2]
            / "custom_components"
            / "better_lighting"
            / "services.py"
        ).read_text()
        registered = set(
            re.findall(r"async_register\(\s*DOMAIN,\s*(SERVICE_\w+)", source, re.S)
        )
        teardown = source.split("def async_remove_services")[1]
        removed = set(re.findall(r"(SERVICE_\w+)", teardown))
        assert registered <= removed, f"never removed: {sorted(registered - removed)}"

    async def test_leftovers_from_an_older_version_are_offered_for_removal(
        self, hass: HomeAssistant
    ) -> None:
        """They are inert, but inert is not gone."""
        from homeassistant.config_entries import ConfigSubentry

        from custom_components.better_lighting.const import SubentryType

        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry())
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data={"name": "Old scene"},
                subentry_type=SubentryType.SCENE.value,
                title="Old scene",
                unique_id=None,
            ),
        )
        await hass.async_block_till_done()

        issues = _our_issues(hass)
        assert any("legacy_subentries" in issue for issue in issues), issues

    async def test_fixing_it_removes_them(self, hass: HomeAssistant) -> None:
        from homeassistant.config_entries import ConfigSubentry

        from custom_components.better_lighting.const import SubentryType
        from custom_components.better_lighting.repairs import async_create_fix_flow

        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        entry = await setup_hub(hass, hub_entry())
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data={"name": "Old scene"},
                subentry_type=SubentryType.SCENE.value,
                title="Old scene",
                unique_id=None,
            ),
        )
        await hass.async_block_till_done()

        flow = await async_create_fix_flow(
            hass, "legacy_subentries", {"entry_id": entry.entry_id}
        )
        flow.hass = hass
        await flow.async_step_confirm({})
        await hass.async_block_till_done()

        assert not [
            sub
            for sub in entry.subentries.values()
            if sub.subentry_type == SubentryType.SCENE.value
        ]
        assert not any("legacy_subentries" in issue for issue in _our_issues(hass))

    async def test_a_clean_install_is_not_nagged(self, hass: HomeAssistant) -> None:
        await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
        await setup_hub(hass, hub_entry())

        assert not any("legacy_subentries" in issue for issue in _our_issues(hass))
