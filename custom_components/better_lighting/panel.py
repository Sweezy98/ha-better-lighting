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

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.setup import async_setup_component
from homeassistant.util import ulid as ulid_util

from .const import (
    CONF_NAME,
    CONF_SCENE_ID,
    CONF_SCENE_LIGHTS,
    CONF_ZONE_SCENES,
    DOMAIN,
    SubentryType,
)
from .models import zone_scene
from .render import ZoneMode

_LOGGER = logging.getLogger(__name__)

PANEL_URL = f"/{DOMAIN}_panel"
PANEL_PATH = f"{DOMAIN}-panel"
PANEL_FILE = "better_lighting_panel.js"
ELEMENT = "better-lighting-panel"

# The id a draft is applied under while it is being edited. Reserved: a scene
# the user saves never takes it, so a preview can always be told apart from
# the real thing and cleaned up by name.
DRAFT_ID = "__draft__"


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Serve the panel's JavaScript and put it in the sidebar.

    Deliberately not a manifest dependency, and deliberately forgiving. The
    panel is a nicety; the lighting is not. A headless install, or any future
    frontend that will not load, should cost the user their scene editor and
    nothing else.
    """
    if DOMAIN in hass.data.get("frontend_panels", {}):
        return
    if not await async_setup_component(hass, "panel_custom", {}):
        _LOGGER.warning(
            "The frontend is unavailable, so the Better Lighting panel was not "
            "registered. Everything else works; scenes stay editable from "
            "Settings"
        )
        return

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"{PANEL_URL}/{PANEL_FILE}",
                str(Path(__file__).parent / "www" / PANEL_FILE),
                # Cache-busting is handled by the query string on the module
                # URL, so the file itself may be cached.
                True,
            )
        ]
    )

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=DOMAIN,
        webcomponent_name=ELEMENT,
        module_url=f"{PANEL_URL}/{PANEL_FILE}",
        sidebar_title="Better Lighting",
        sidebar_icon="mdi:lightbulb-group",
        require_admin=True,
    )


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register the panel's websocket API."""
    for handler in (
        websocket_config,
        websocket_preview,
        websocket_stop_preview,
        websocket_save_scene,
        websocket_delete_scene,
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
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SubentryType.ZONE.value:
            continue
        rooms.append(
            {
                "id": subentry.subentry_id,
                "name": subentry.data.get(CONF_NAME) or subentry.title,
                "lights": list(subentry.data.get("lights") or ()),
                "scenes": [
                    dict(s) for s in (subentry.data.get(CONF_ZONE_SCENES) or ())
                ],
            }
        )
    connection.send_result(msg["id"], {"rooms": rooms})


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
