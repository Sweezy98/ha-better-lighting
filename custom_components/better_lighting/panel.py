"""A sidebar panel for building scenes while watching the room.

A config flow is a sequence of forms. That is the right shape for "which
helper says the house is asleep" and the wrong one for "make that strip a
little more orange", where the only way to judge the answer is to look at the
wall. Home Assistant gives forms a colour picker and a temperature slider but
not the wheel from the light dialog, and it has no way at all to show the room
changing as you drag.

So scenes get a page of their own instead: the rooms down one side, the lights
of the selected room down the other, and every change applied to the actual
bulbs as it is made. The panel is plain JavaScript served straight from the
integration -- no build step, because HACS copies files and does not run one.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.setup import async_setup_component
from homeassistant.util import ulid as ulid_util

from . import panel_schema
from .config_flow import validate_zone_lights
from .const import (
    CONF_LIGHTS,
    CONF_NAME,
    CONF_RULES,
    CONF_SCENE_ID,
    CONF_SCENE_LIGHTS,
    CONF_STATES,
    CONF_SWITCH_ID,
    CONF_ZONE_PROFILES,
    CONF_ZONE_SCENES,
    CONF_ZONE_SWITCHES,
    CONTROLLER_SPECS,
    DOMAIN,
    HUB_SPECS,
    LIGHT_PROFILE_SPECS,
    MODE_SPECS,
    ZONE_SCENE_SPECS,
    ZONE_SPECS,
    SubentryType,
)
from .models import zone_scene
from .render import ZoneMode
from .schemas import post_validate

_LOGGER = logging.getLogger(__name__)

PANEL_URL = f"/{DOMAIN}_panel"
PANEL_PATH = f"{DOMAIN}-panel"
PANEL_FILE = "better_lighting_panel.js"
ELEMENT = "better-lighting-panel"

# The id a draft is applied under while it is being edited. Reserved: a scene
# the user saves never takes it, so a preview can always be told apart from
# the real thing and cleaned up by name.
DRAFT_ID = "__draft__"

# Where we note that the panel's route is already on the HTTP app.
_STATIC_REGISTERED = f"{DOMAIN}_panel_static"


def _fingerprint() -> str:
    """A short hash of the panel script, for the module URL.

    The browser -- and Home Assistant's own service worker -- are told to
    cache this file hard, so the URL has to change when the file does or an
    upgrade silently keeps serving the old page. Hashing the contents rather
    than using the version means it is right even when the same version is
    installed twice, which is what happens to anybody tracking main.
    """
    path = Path(__file__).parent / "www" / PANEL_FILE
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:  # pragma: no cover - the file ships with the integration
        return "dev"


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Serve the panel's JavaScript and put it in the sidebar.

    Deliberately not a manifest dependency, and deliberately forgiving. The
    panel is a nicety; the lighting is not. A headless install, or any future
    frontend that will not load, should cost the user their scene editor and
    nothing else.
    """
    if not await async_setup_component(hass, "panel_custom", {}):
        _LOGGER.warning(
            "The frontend is unavailable, so the Better Lighting panel was not "
            "registered. Everything else works; scenes stay editable from "
            "Settings"
        )
        return

    fingerprint = await hass.async_add_executor_job(_fingerprint)
    module_url = f"{PANEL_URL}/{PANEL_FILE}?v={fingerprint}"

    if (existing := hass.data.get("frontend_panels", {}).get(DOMAIN)) is not None:
        if (existing.config or {}).get("_panel_custom", {}).get(
            "module_url"
        ) == module_url:
            return
        # Registered from an older copy of the script. Replace it rather than
        # leaving the sidebar pointing at a URL nothing will fetch again.
        frontend.async_remove_panel(hass, DOMAIN)

    # The route is registered once per run; aiohttp refuses a second one for
    # the same path, and the fingerprint lives in the query string anyway.
    if not hass.data.get(_STATIC_REGISTERED):
        hass.data[_STATIC_REGISTERED] = True
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    f"{PANEL_URL}/{PANEL_FILE}",
                    str(Path(__file__).parent / "www" / PANEL_FILE),
                    # Safe to cache hard: the URL carries the file's
                    # fingerprint, so a changed file is a different URL.
                    True,
                )
            ]
        )

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=DOMAIN,
        webcomponent_name=ELEMENT,
        module_url=module_url,
        sidebar_title="Better Lighting",
        sidebar_icon="mdi:lightbulb-group",
        require_admin=True,
    )


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register the panel's websocket API."""
    for handler in (
        websocket_config,
        websocket_schema,
        websocket_preview,
        websocket_stop_preview,
        websocket_save_scene,
        websocket_delete_scene,
        websocket_save_hub,
        websocket_save_zone,
        websocket_delete_zone,
        websocket_save_mode,
        websocket_delete_mode,
        websocket_save_collection,
    ):
        websocket_api.async_register_command(hass, handler)


def _entry(hass: HomeAssistant) -> Any:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def _zone_subentry(entry: Any, zone_id: str) -> Any:
    subentry = entry.subentries.get(zone_id)
    if subentry is None or subentry.subentry_type != SubentryType.ZONE.value:
        return None
    return subentry


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/config"})
@callback
def websocket_config(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The rooms, their lights and their scenes, as the panel needs them."""
    entry = _entry(hass)
    if entry is None:
        connection.send_result(msg["id"], {"rooms": []})
        return

    rooms = []
    modes = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SubentryType.ZONE.value:
            rooms.append(
                {
                    "id": subentry.subentry_id,
                    "name": subentry.data.get(CONF_NAME) or subentry.title,
                    "lights": list(subentry.data.get("lights") or ()),
                    "scenes": [
                        dict(s) for s in (subentry.data.get(CONF_ZONE_SCENES) or ())
                    ],
                    # Everything else the room stores, so the panel can edit
                    # any of it without a round trip per screen.
                    "data": dict(subentry.data),
                }
            )
        elif subentry.subentry_type == SubentryType.MODE.value:
            modes.append(
                {
                    "id": subentry.subentry_id,
                    "name": subentry.data.get(CONF_NAME) or subentry.title,
                    "data": dict(subentry.data),
                }
            )
    connection.send_result(
        msg["id"],
        {"rooms": rooms, "modes": modes, "hub": dict(entry.options)},
    )


async def _async_apply_draft(
    hass: HomeAssistant, zone_id: str, scene: dict[str, Any]
) -> bool:
    """Show a draft on the actual bulbs.

    Registered under a reserved id rather than saved, so what is on the wall
    is always something the renderer produced -- the preview and the saved
    scene cannot drift apart, because they are the same code path.
    """
    entry = _entry(hass)
    if entry is None:
        return False
    controller = entry.runtime_data.controllers.get(zone_id)
    if controller is None:
        return False

    draft = dict(scene)
    draft[CONF_SCENE_ID] = DRAFT_ID
    draft.setdefault(CONF_NAME, "Draft")
    controller.scenes[DRAFT_ID] = zone_scene(draft, zone_id)
    await controller.async_set_mode(ZoneMode.SCENE, DRAFT_ID)
    return True


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/preview",
        vol.Required("zone_id"): str,
        vol.Required("scene"): dict,
    }
)
@websocket_api.async_response
async def websocket_preview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put a draft on the wall."""
    ok = await _async_apply_draft(hass, msg["zone_id"], msg["scene"])
    connection.send_result(msg["id"], {"applied": ok})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/stop_preview", vol.Required("zone_id"): str}
)
@websocket_api.async_response
async def websocket_stop_preview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put the room back on the curve and forget the draft."""
    entry = _entry(hass)
    controller = entry.runtime_data.controllers.get(msg["zone_id"]) if entry else None
    if controller is not None:
        controller.scenes.pop(DRAFT_ID, None)
        await controller.async_set_adaptive()
    connection.send_result(msg["id"], {"stopped": controller is not None})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_scene",
        vol.Required("zone_id"): str,
        vol.Required("scene"): dict,
    }
)
@websocket_api.async_response
async def websocket_save_scene(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Write a scene into its room, adding or replacing by id."""
    entry = _entry(hass)
    subentry = _zone_subentry(entry, msg["zone_id"]) if entry else None
    if subentry is None:
        connection.send_error(msg["id"], "not_found", "No such room")
        return

    scene = dict(msg["scene"])
    scene.pop(DRAFT_ID, None)
    if not scene.get(CONF_SCENE_ID) or scene[CONF_SCENE_ID] == DRAFT_ID:
        scene[CONF_SCENE_ID] = ulid_util.ulid_now()
    scene.setdefault(CONF_NAME, "Scene")
    scene.setdefault(CONF_SCENE_LIGHTS, {})

    scenes = [dict(s) for s in (subentry.data.get(CONF_ZONE_SCENES) or ())]
    for index, existing in enumerate(scenes):
        if existing.get(CONF_SCENE_ID) == scene[CONF_SCENE_ID]:
            scenes[index] = scene
            break
    else:
        scenes.append(scene)

    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, CONF_ZONE_SCENES: scenes}
    )
    connection.send_result(msg["id"], {"scene_id": scene[CONF_SCENE_ID]})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_scene",
        vol.Required("zone_id"): str,
        vol.Required("scene_id"): str,
    }
)
@websocket_api.async_response
async def websocket_delete_scene(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _entry(hass)
    subentry = _zone_subentry(entry, msg["zone_id"]) if entry else None
    if subentry is None:
        connection.send_error(msg["id"], "not_found", "No such room")
        return

    scenes = [
        dict(s)
        for s in (subentry.data.get(CONF_ZONE_SCENES) or ())
        if s.get(CONF_SCENE_ID) != msg["scene_id"]
    ]
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, CONF_ZONE_SCENES: scenes}
    )
    connection.send_result(msg["id"], {"deleted": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schema",
        vol.Optional("language", default="en"): str,
    }
)
@callback
def websocket_schema(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every form the panel can draw, described from the same tables the
    config flow renders."""
    connection.send_result(msg["id"], panel_schema.schema(msg["language"]))


def _clean(specs: Any, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Run a payload through the same validation the forms use."""
    return post_validate(specs, data)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/save_hub", vol.Required("options"): dict}
)
@websocket_api.async_response
async def websocket_save_hub(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Not set up")
        return
    cleaned, errors = _clean(HUB_SPECS, msg["options"])
    if errors:
        connection.send_error(msg["id"], "invalid", json.dumps(errors))
        return
    hass.config_entries.async_update_entry(entry, options={**entry.options, **cleaned})
    connection.send_result(msg["id"], {"saved": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_zone",
        vol.Optional("zone_id"): vol.Any(str, None),
        vol.Required("data"): dict,
    }
)
@websocket_api.async_response
async def websocket_save_zone(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Add a room, or replace one room's settings wholesale."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Not set up")
        return

    zone_id = msg.get("zone_id")
    cleaned, errors = _clean(ZONE_SPECS, msg["data"])
    errors |= validate_zone_lights(
        entry, cleaned.get(CONF_LIGHTS) or [], exclude_subentry_id=zone_id
    )
    if errors:
        connection.send_error(msg["id"], "invalid", json.dumps(errors))
        return

    title = cleaned.get(CONF_NAME) or "Room"
    if zone_id:
        subentry = _zone_subentry(entry, zone_id)
        if subentry is None:
            connection.send_error(msg["id"], "not_found", "No such room")
            return
        # The collections a room owns are edited by their own commands, so a
        # settings save must not drop them.
        keep = {
            key: subentry.data[key]
            for key in (CONF_ZONE_SCENES, CONF_ZONE_SWITCHES, CONF_ZONE_PROFILES)
            if key in subentry.data
        }
        hass.config_entries.async_update_subentry(
            entry, subentry, data={**keep, **cleaned}, title=title
        )
    else:
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data=cleaned,
                subentry_type=SubentryType.ZONE.value,
                title=title,
                unique_id=None,
            ),
        )
    connection.send_result(msg["id"], {"saved": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/delete_zone", vol.Required("zone_id"): str}
)
@websocket_api.async_response
async def websocket_delete_zone(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _entry(hass)
    if entry is None or msg["zone_id"] not in entry.subentries:
        connection.send_error(msg["id"], "not_found", "No such room")
        return
    hass.config_entries.async_remove_subentry(entry, msg["zone_id"])
    connection.send_result(msg["id"], {"deleted": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_mode",
        vol.Optional("mode_id"): vol.Any(str, None),
        vol.Required("data"): dict,
    }
)
@websocket_api.async_response
async def websocket_save_mode(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Not set up")
        return

    cleaned, errors = _clean(MODE_SPECS, msg["data"])
    if not cleaned.get(CONF_STATES):
        errors[CONF_STATES] = "no_states"
    if errors:
        connection.send_error(msg["id"], "invalid", json.dumps(errors))
        return
    # Rules travel with the mode: they are edited on the same screen.
    cleaned[CONF_RULES] = list(msg["data"].get(CONF_RULES) or ())

    title = cleaned.get(CONF_NAME) or "Mode"
    mode_id = msg.get("mode_id")
    if mode_id:
        subentry = entry.subentries.get(mode_id)
        if subentry is None or subentry.subentry_type != SubentryType.MODE.value:
            connection.send_error(msg["id"], "not_found", "No such mode")
            return
        hass.config_entries.async_update_subentry(
            entry, subentry, data=cleaned, title=title
        )
    else:
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data=cleaned,
                subentry_type=SubentryType.MODE.value,
                title=title,
                unique_id=None,
            ),
        )
    connection.send_result(msg["id"], {"saved": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/delete_mode", vol.Required("mode_id"): str}
)
@websocket_api.async_response
async def websocket_delete_mode(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _entry(hass)
    if entry is None or msg["mode_id"] not in entry.subentries:
        connection.send_error(msg["id"], "not_found", "No such mode")
        return
    hass.config_entries.async_remove_subentry(entry, msg["mode_id"])
    connection.send_result(msg["id"], {"deleted": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_zone_collection",
        vol.Required("zone_id"): str,
        vol.Required("key"): vol.In(
            [CONF_ZONE_SCENES, CONF_ZONE_SWITCHES, CONF_ZONE_PROFILES]
        ),
        vol.Required("items"): list,
    }
)
@websocket_api.async_response
async def websocket_save_collection(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Replace one of a room's lists: its scenes, switches or calibrations.

    Wholesale rather than per item, because the panel edits the list it was
    given and the order within it is meaningful -- a switch cycles its scenes
    in the order they are stored.
    """
    entry = _entry(hass)
    subentry = _zone_subentry(entry, msg["zone_id"]) if entry else None
    if subentry is None:
        connection.send_error(msg["id"], "not_found", "No such room")
        return

    specs = {
        CONF_ZONE_SCENES: ZONE_SCENE_SPECS,
        CONF_ZONE_SWITCHES: CONTROLLER_SPECS,
        CONF_ZONE_PROFILES: LIGHT_PROFILE_SPECS,
    }[msg["key"]]
    id_key = {
        CONF_ZONE_SCENES: CONF_SCENE_ID,
        CONF_ZONE_SWITCHES: CONF_SWITCH_ID,
    }.get(msg["key"])

    items = []
    for raw in msg["items"]:
        cleaned, errors = post_validate(specs, raw)
        if errors:
            connection.send_error(msg["id"], "invalid", json.dumps(errors))
            return
        # Anything the form does not describe -- a scene's per-light entries,
        # a switch's running order -- is carried across untouched.
        merged = {**raw, **cleaned}
        if id_key and not merged.get(id_key):
            merged[id_key] = ulid_util.ulid_now()
        items.append(merged)

    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, msg["key"]: items}
    )
    connection.send_result(msg["id"], {"saved": len(items)})


@callback
def async_remove_panel(hass: HomeAssistant) -> None:
    """Take the page out of the sidebar.

    Panels are not tied to a config entry, so nothing removes this one for
    us: uninstalling the integration otherwise left a sidebar item that
    loaded a script for an integration that was no longer there.
    """
    if DOMAIN in hass.data.get("frontend_panels", {}):
        frontend.async_remove_panel(hass, DOMAIN)
