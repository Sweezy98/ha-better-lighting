"""Telling the user when their configuration has come apart.

Home Assistant offers no way to refuse the deletion of a config subentry, so a
scene can be removed while a switch is still cycling through it and a room is
still using it as its night scene. The runtime handles that leniently -- a
missing scene is skipped rather than crashed on -- but silently shortening
somebody's cycle is not something to do without saying so.

Each dangling reference becomes a repair issue naming both ends, so the fix is
obvious rather than archaeological.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, SubentryType
from .cycle import ADAPTIVE_STEP

if TYPE_CHECKING:
    from . import BetterLightingRuntime

_LOGGER = logging.getLogger(__name__)

ISSUE_MISSING_SCENE = "missing_scene"
ISSUE_MISSING_ROOM = "missing_zone"
ISSUE_SHARED_LIGHT = "shared_light"


@callback
def async_check_references(
    hass: HomeAssistant, entry_id: str, runtime: BetterLightingRuntime
) -> None:
    """Raise, or clear, a repair issue for every broken reference."""
    known_scenes = set(runtime.scenes)
    known_rooms = set(runtime.rooms)
    found: set[str] = set()

    def _raise(
        issue_id: str, translation_key: str, placeholders: dict[str, str]
    ) -> None:
        found.add(issue_id)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders=placeholders,
        )

    for room in runtime.rooms.values():
        for label, scene_id in (
            ("night scene", room.night_scene_id),
            ("insect scene", room.insect_scene_id),
            ("presence scene", room.presence_on_scene_id),
        ):
            if scene_id and scene_id not in known_scenes:
                _raise(
                    f"{ISSUE_MISSING_SCENE}_{room.subentry_id}_{label.replace(' ', '_')}",
                    ISSUE_MISSING_SCENE,
                    {"holder": f"the {label} of {room.name}", "what": "a scene"},
                )

    for controller in runtime.switches.values():
        if controller.room_id not in known_rooms:
            _raise(
                f"{ISSUE_MISSING_ROOM}_{controller.subentry_id}",
                ISSUE_MISSING_ROOM,
                {"holder": f"the switch {controller.name}", "what": "a room"},
            )
        # Adaptive is a step in the list, written the way a scene is but
        # answering to no scene -- so it is not a reference that can dangle.
        missing = [
            s
            for s in controller.scene_order
            if s != ADAPTIVE_STEP and s not in known_scenes
        ]
        if missing:
            _raise(
                f"{ISSUE_MISSING_SCENE}_{controller.subentry_id}",
                ISSUE_MISSING_SCENE,
                {
                    "holder": f"the cycle of {controller.name}",
                    "what": f"{len(missing)} scene(s)",
                },
            )

    for mode in runtime.modes.values():
        for rule in mode.rules:
            if rule.scene_id and rule.scene_id not in known_scenes:
                _raise(
                    f"{ISSUE_MISSING_SCENE}_{mode.subentry_id}",
                    ISSUE_MISSING_SCENE,
                    {"holder": f"a rule of {mode.name}", "what": "a scene"},
                )
            if missing_rooms := rule.rooms - known_rooms:
                _raise(
                    f"{ISSUE_MISSING_ROOM}_{mode.subentry_id}",
                    ISSUE_MISSING_ROOM,
                    {
                        "holder": f"a rule of {mode.name}",
                        "what": f"{len(missing_rooms)} room(s)",
                    },
                )

    # Two rooms claiming one light is refused by the config flow, but a light
    # can still be renamed into a collision, or config edited by hand.
    claimed: dict[str, str] = {}
    for room in runtime.rooms.values():
        for entity_id in room.lights:
            if (other := claimed.get(entity_id)) is not None:
                _raise(
                    f"{ISSUE_SHARED_LIGHT}_{entity_id}",
                    ISSUE_SHARED_LIGHT,
                    {"light": entity_id, "first": other, "second": room.name},
                )
            claimed[entity_id] = room.name

    _async_clear_stale(hass, entry_id, found)


@callback
def _async_clear_stale(
    hass: HomeAssistant, entry_id: str, still_broken: set[str]
) -> None:
    """Remove issues the user has since fixed."""
    registry = ir.async_get(hass)
    for issue in list(registry.issues.values()):
        if issue.domain != DOMAIN or issue.issue_id in still_broken:
            continue
        if issue.issue_id.startswith(
            (ISSUE_MISSING_SCENE, ISSUE_MISSING_ROOM, ISSUE_SHARED_LIGHT)
        ):
            ir.async_delete_issue(hass, DOMAIN, issue.issue_id)


ISSUE_LEGACY_SUBENTRIES = "legacy_subentries"
# The three kinds that moved inside a room in 0.10, and are inert wherever
# they still exist.
LEGACY_TYPES: tuple[tuple[str, str], ...] = (
    (SubentryType.SCENE.value, "scene"),
    (SubentryType.LIGHT_PROFILE.value, "light calibration"),
    (SubentryType.CONTROLLER.value, "light switch"),
)


@callback
def async_check_legacy(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Offer to clear out objects an upgrade left inert.

    Scenes, calibrations and switches became part of a room in 0.10, and the
    old ones are ignored rather than migrated. Ignored is not gone: they stay
    in the config file and on the hub page, doing nothing, for as long as the
    install lasts. Offered as a repair rather than swept away silently,
    because deleting somebody's configuration without asking is not ours to
    decide -- even configuration that no longer does anything.
    """
    leftovers = [
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type in {kind for kind, _ in LEGACY_TYPES}
    ]
    if not leftovers:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_LEGACY_SUBENTRIES)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_LEGACY_SUBENTRIES,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_LEGACY_SUBENTRIES,
        translation_placeholders={
            "count": str(len(leftovers)),
            "names": ", ".join(sorted(sub.title for sub in leftovers)),
        },
        data={"entry_id": entry.entry_id},
    )


class LegacySubentriesRepair(RepairsFlow):
    """Deletes the objects an upgrade left behind, once confirmed."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is not None:
            kinds = {kind for kind, _ in LEGACY_TYPES}
            for subentry_id, subentry in list(entry.subentries.items()):
                if subentry.subentry_type in kinds:
                    self.hass.config_entries.async_remove_subentry(entry, subentry_id)
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_LEGACY_SUBENTRIES)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Home Assistant asks for the flow that fixes one of our issues."""
    return LegacySubentriesRepair((data or {}).get("entry_id", ""))
