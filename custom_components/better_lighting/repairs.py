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
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from . import BetterLightingRuntime

_LOGGER = logging.getLogger(__name__)

ISSUE_MISSING_SCENE = "missing_scene"
ISSUE_MISSING_ZONE = "missing_zone"
ISSUE_SHARED_LIGHT = "shared_light"


@callback
def async_check_references(
    hass: HomeAssistant, entry_id: str, runtime: BetterLightingRuntime
) -> None:
    """Raise, or clear, a repair issue for every broken reference."""
    known_scenes = set(runtime.scenes)
    known_zones = set(runtime.zones)
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

    for zone in runtime.zones.values():
        for label, scene_id in (
            ("night scene", zone.night_scene_id),
            ("insect scene", zone.insect_scene_id),
            ("presence scene", zone.presence_on_scene_id),
        ):
            if scene_id and scene_id not in known_scenes:
                _raise(
                    f"{ISSUE_MISSING_SCENE}_{zone.subentry_id}_{label.replace(' ', '_')}",
                    ISSUE_MISSING_SCENE,
                    {"holder": f"the {label} of {zone.name}", "what": "a scene"},
                )

    for controller in runtime.switches.values():
        if controller.zone_id not in known_zones:
            _raise(
                f"{ISSUE_MISSING_ZONE}_{controller.subentry_id}",
                ISSUE_MISSING_ZONE,
                {"holder": f"the switch {controller.name}", "what": "a room"},
            )
        missing = [s for s in controller.scene_order if s not in known_scenes]
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
            if missing_zones := rule.zones - known_zones:
                _raise(
                    f"{ISSUE_MISSING_ZONE}_{mode.subentry_id}",
                    ISSUE_MISSING_ZONE,
                    {
                        "holder": f"a rule of {mode.name}",
                        "what": f"{len(missing_zones)} room(s)",
                    },
                )

    # Two rooms claiming one light is refused by the config flow, but a light
    # can still be renamed into a collision, or config edited by hand.
    claimed: dict[str, str] = {}
    for zone in runtime.zones.values():
        for entity_id in zone.lights:
            if (other := claimed.get(entity_id)) is not None:
                _raise(
                    f"{ISSUE_SHARED_LIGHT}_{entity_id}",
                    ISSUE_SHARED_LIGHT,
                    {"light": entity_id, "first": other, "second": zone.name},
                )
            claimed[entity_id] = zone.name

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
            (ISSUE_MISSING_SCENE, ISSUE_MISSING_ZONE, ISSUE_SHARED_LIGHT)
        ):
            ir.async_delete_issue(hass, DOMAIN, issue.issue_id)
