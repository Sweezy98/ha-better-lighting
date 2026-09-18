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
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components import scene as scene_component
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util import ulid as ulid_util

from . import adaptive, panel_schema
from .config_flow import validate_room_lights
from .const import (
    COLOR_FORMAT_NONE,
    CONF_ADAPTIVE_POSITION,
    CONF_BRIGHTNESS_PCT,
    CONF_COLOR_FORMAT,
    CONF_COLOR_TEMP_KELVIN,
    CONF_GROUP_ID,
    CONF_ICON,
    CONF_IGNORE_PRESENCE,
    CONF_LIGHT_ACTION,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_ON_LIGHTS_ONLY,
    CONF_ON_UNSUPPORTED_COLOR,
    CONF_OTHERS,
    CONF_RGB_COLOR,
    CONF_ROOM_GROUPS,
    CONF_ROOM_PROFILES,
    CONF_ROOM_SCENES,
    CONF_ROOM_SWITCHES,
    CONF_ROOM_ZONES,
    CONF_RULES,
    CONF_SCENE_ID,
    CONF_SCENE_LIGHTS,
    CONF_SCENE_ORDER,
    CONF_SCENE_ORDER_EXCLUDED,
    CONF_STATES,
    CONF_SWITCH_ID,
    CONF_TRANSITION,
    CONF_ZONE_ID,
    CONTROLLER_SPECS,
    DOMAIN,
    HUB_SPECS,
    LIGHT_GROUP_SPECS,
    LIGHT_PROFILE_SPECS,
    MODE_SPECS,
    ROOM_SCENE_SPECS,
    ROOM_SPECS,
    ROOM_ZONE_SPECS,
    SubentryType,
)
from .cycle import AdaptivePosition
from .groups import find_cycle
from .models import effective_scene_order, room_light_group, room_scene, room_zone
from .render import RoomMode, Trigger
from .schemas import post_validate
from .zones import overlapping_lights

_LOGGER = logging.getLogger(__name__)

PANEL_URL = f"/{DOMAIN}_panel"
PANEL_PATH = f"{DOMAIN}-panel"
PANEL_FILE = "better_lighting_panel.js"
ELEMENT = "better-lighting-panel"

# The id a draft is applied under while it is being edited. Reserved: a scene
# the user saves never takes it, so a preview can always be told apart from
# the real thing and cleaned up by name.
DRAFT_ID = "__draft__"

# Points sampled across the day for the curve graph. 96 is every quarter of an
# hour: fine enough to show the shape around sunset, cheap enough to compute on
# every open.
_CURVE_STEPS = 96

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
        websocket_save_room,
        websocket_delete_room,
        websocket_save_mode,
        websocket_delete_mode,
        websocket_save_collection,
        websocket_importable_scenes,
        websocket_import_scene,
        websocket_version,
        websocket_diagnostics,
        websocket_activity,
        websocket_curve,
    ):
        websocket_api.async_register_command(hass, handler)


def _entry(hass: HomeAssistant) -> Any:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def _room_subentry(entry: Any, room_id: str) -> Any:
    subentry = entry.subentries.get(room_id)
    if subentry is None or subentry.subentry_type != SubentryType.ROOM.value:
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
        if subentry.subentry_type == SubentryType.ROOM.value:
            scenes = [dict(s) for s in (subentry.data.get(CONF_ROOM_SCENES) or ())]
            scene_ids = [s.get(CONF_SCENE_ID) for s in scenes if s.get(CONF_SCENE_ID)]
            rooms.append(
                {
                    "id": subentry.subentry_id,
                    "name": subentry.data.get(CONF_NAME) or subentry.title,
                    "lights": list(subentry.data.get("lights") or ()),
                    "scenes": scenes,
                    # What each switch actually cycles, worked out by the same
                    # rule the runtime uses: a switch with no list of its own
                    # cycles the whole room, and one with a list gains any
                    # scene made since at the end of it.
                    "switch_orders": [
                        list(
                            effective_scene_order(
                                switch.get(CONF_SCENE_ORDER) or (),
                                switch.get(CONF_SCENE_ORDER_EXCLUDED) or (),
                                scene_ids,
                                # Same argument the runtime reads it with, or
                                # the page would show adaptive somewhere the
                                # switch does not actually have it.
                                AdaptivePosition(
                                    switch.get(
                                        CONF_ADAPTIVE_POSITION,
                                        AdaptivePosition.FIRST.value,
                                    )
                                ),
                            )
                        )
                        for switch in (subentry.data.get(CONF_ROOM_SWITCHES) or ())
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
    hass: HomeAssistant, room_id: str, scene: dict[str, Any]
) -> bool:
    """Show a draft on the actual bulbs.

    Registered under a reserved id rather than saved, so what is on the wall
    is always something the renderer produced -- the preview and the saved
    scene cannot drift apart, because they are the same code path.
    """
    entry = _entry(hass)
    if entry is None:
        return False
    controller = entry.runtime_data.controllers.get(room_id)
    if controller is None:
        return False

    draft = dict(scene)
    draft[CONF_SCENE_ID] = DRAFT_ID
    draft.setdefault(CONF_NAME, "Draft")
    controller.scenes[DRAFT_ID] = room_scene(draft, room_id)
    await controller.async_set_mode(RoomMode.SCENE, DRAFT_ID)
    return True


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/preview",
        vol.Required("room_id"): str,
        vol.Required("scene"): dict,
    }
)
@websocket_api.async_response
async def websocket_preview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put a draft on the wall."""
    ok = await _async_apply_draft(hass, msg["room_id"], msg["scene"])
    connection.send_result(msg["id"], {"applied": ok})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/stop_preview", vol.Required("room_id"): str}
)
@websocket_api.async_response
async def websocket_stop_preview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put the room back on the curve and forget the draft."""
    entry = _entry(hass)
    controller = entry.runtime_data.controllers.get(msg["room_id"]) if entry else None
    if controller is not None:
        controller.scenes.pop(DRAFT_ID, None)
        await controller.async_set_adaptive()
    connection.send_result(msg["id"], {"stopped": controller is not None})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_scene",
        vol.Required("room_id"): str,
        vol.Required("scene"): dict,
    }
)
@websocket_api.async_response
async def websocket_save_scene(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Write a scene into its room, adding or replacing by id."""
    entry = _entry(hass)
    subentry = _room_subentry(entry, msg["room_id"]) if entry else None
    if subentry is None:
        connection.send_error(msg["id"], "not_found", "No such room")
        return

    scene = dict(msg["scene"])
    scene.pop(DRAFT_ID, None)
    if not scene.get(CONF_SCENE_ID) or scene[CONF_SCENE_ID] == DRAFT_ID:
        scene[CONF_SCENE_ID] = ulid_util.ulid_now()
    scene.setdefault(CONF_NAME, "Scene")
    scene.setdefault(CONF_SCENE_LIGHTS, {})

    scenes = [dict(s) for s in (subentry.data.get(CONF_ROOM_SCENES) or ())]
    for index, existing in enumerate(scenes):
        if existing.get(CONF_SCENE_ID) == scene[CONF_SCENE_ID]:
            scenes[index] = scene
            break
    else:
        scenes.append(scene)

    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, CONF_ROOM_SCENES: scenes}
    )
    connection.send_result(msg["id"], {"scene_id": scene[CONF_SCENE_ID]})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_scene",
        vol.Required("room_id"): str,
        vol.Required("scene_id"): str,
    }
)
@websocket_api.async_response
async def websocket_delete_scene(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _entry(hass)
    subentry = _room_subentry(entry, msg["room_id"]) if entry else None
    if subentry is None:
        connection.send_error(msg["id"], "not_found", "No such room")
        return

    scenes = [
        dict(s)
        for s in (subentry.data.get(CONF_ROOM_SCENES) or ())
        if s.get(CONF_SCENE_ID) != msg["scene_id"]
    ]
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, CONF_ROOM_SCENES: scenes}
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
        vol.Required("type"): f"{DOMAIN}/save_room",
        vol.Optional("room_id"): vol.Any(str, None),
        vol.Required("data"): dict,
    }
)
@websocket_api.async_response
async def websocket_save_room(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Add a room, or replace one room's settings wholesale."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Not set up")
        return

    room_id = msg.get("room_id")
    cleaned, errors = _clean(ROOM_SPECS, msg["data"])
    errors |= validate_room_lights(
        entry, cleaned.get(CONF_LIGHTS) or [], exclude_subentry_id=room_id
    )
    if errors:
        connection.send_error(msg["id"], "invalid", json.dumps(errors))
        return

    title = cleaned.get(CONF_NAME) or "Room"
    if room_id:
        subentry = _room_subentry(entry, room_id)
        if subentry is None:
            connection.send_error(msg["id"], "not_found", "No such room")
            return
        # The collections a room owns are edited by their own commands, so a
        # settings save must not drop them.
        keep = {
            key: subentry.data[key]
            for key in (CONF_ROOM_SCENES, CONF_ROOM_SWITCHES, CONF_ROOM_PROFILES)
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
                subentry_type=SubentryType.ROOM.value,
                title=title,
                unique_id=None,
            ),
        )
    connection.send_result(msg["id"], {"saved": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/delete_room", vol.Required("room_id"): str}
)
@websocket_api.async_response
async def websocket_delete_room(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _entry(hass)
    if entry is None or msg["room_id"] not in entry.subentries:
        connection.send_error(msg["id"], "not_found", "No such room")
        return
    hass.config_entries.async_remove_subentry(entry, msg["room_id"])
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
        vol.Required("type"): f"{DOMAIN}/save_room_collection",
        vol.Required("room_id"): str,
        vol.Required("key"): vol.In(
            [
                CONF_ROOM_SCENES,
                CONF_ROOM_SWITCHES,
                CONF_ROOM_PROFILES,
                CONF_ROOM_GROUPS,
                CONF_ROOM_ZONES,
            ]
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
    subentry = _room_subentry(entry, msg["room_id"]) if entry else None
    if subentry is None:
        connection.send_error(msg["id"], "not_found", "No such room")
        return

    specs = {
        CONF_ROOM_SCENES: ROOM_SCENE_SPECS,
        CONF_ROOM_SWITCHES: CONTROLLER_SPECS,
        CONF_ROOM_PROFILES: LIGHT_PROFILE_SPECS,
        CONF_ROOM_GROUPS: LIGHT_GROUP_SPECS,
        CONF_ROOM_ZONES: ROOM_ZONE_SPECS,
    }[msg["key"]]
    id_key = {
        CONF_ROOM_SCENES: CONF_SCENE_ID,
        CONF_ROOM_SWITCHES: CONF_SWITCH_ID,
        CONF_ROOM_GROUPS: CONF_GROUP_ID,
        CONF_ROOM_ZONES: CONF_ZONE_ID,
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

    if msg["key"] == CONF_ROOM_ZONES and (
        shared := overlapping_lights([room_zone(item) for item in items])
    ):
        # Two answers to "what should this bulb be doing" is the bug that
        # one light, one room exists to prevent. A zone is the same argument
        # one level down, so it is refused the same way.
        connection.send_error(
            msg["id"],
            "overlap",
            ", ".join(
                f"{light}: {' & '.join(names)}"
                for light, names in sorted(shared.items())
            ),
        )
        return

    if msg["key"] == CONF_ROOM_GROUPS and (loop := _group_cycle(items)):
        # Refused rather than stored: a group that contains itself has no
        # meaning to fall back to, and the room would silently lose every
        # group it has. Named in full, because "this is recursive" does not
        # say which two entries to look at.
        connection.send_error(msg["id"], "cycle", " -> ".join(loop))
        return

    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, msg["key"]: items}
    )
    connection.send_result(msg["id"], {"saved": len(items)})


def _group_cycle(items: list[dict[str, Any]]) -> list[str] | None:
    """The loop in a proposed set of light groups, by name, or None."""
    groups = {
        item[CONF_GROUP_ID]: room_light_group(item)
        for item in items
        if item.get(CONF_GROUP_ID)
    }
    if (cycle := find_cycle(groups)) is None:
        return None
    return [groups[group_id].name or group_id for group_id in cycle]


@callback
def async_remove_panel(hass: HomeAssistant) -> None:
    """Take the page out of the sidebar.

    Panels are not tied to a config entry, so nothing removes this one for
    us: uninstalling the integration otherwise left a sidebar item that
    loaded a script for an integration that was no longer there.
    """
    if DOMAIN in hass.data.get("frontend_panels", {}):
        frontend.async_remove_panel(hass, DOMAIN)


def _scene_entry(state: Any) -> dict[str, Any] | None:
    """One light's target state in a Home Assistant scene, as a scene entry.

    The same shape the panel's capture produces, because it is the same
    question asked of a stored state rather than a live one.
    """
    if state is None:
        return None
    if state.state != "on":
        return {CONF_LIGHT_ACTION: "off"}

    entry: dict[str, Any] = {CONF_LIGHT_ACTION: "apply"}
    if (brightness := state.attributes.get("brightness")) is not None:
        entry[CONF_BRIGHTNESS_PCT] = round(int(brightness) / 255 * 100, 1)
    # A light stored in colour-temp mode carries a derived rgb_color too, and
    # keeping that would freeze a warm white into a slightly-wrong orange.
    if state.attributes.get("color_mode") == "color_temp" and (
        kelvin := state.attributes.get("color_temp_kelvin")
    ):
        entry[CONF_COLOR_FORMAT] = CONF_COLOR_TEMP_KELVIN
        entry[CONF_COLOR_TEMP_KELVIN] = int(kelvin)
    elif (rgb := state.attributes.get("rgb_color")) is not None:
        entry[CONF_COLOR_FORMAT] = CONF_RGB_COLOR
        entry[CONF_RGB_COLOR] = list(rgb)
    else:
        entry[CONF_COLOR_FORMAT] = COLOR_FORMAT_NONE
    return entry


def _ha_scenes(hass: HomeAssistant) -> dict[str, Any]:
    """Home Assistant's own scenes, by entity id, with their stored states.

    Only the ones defined in Home Assistant itself: a scene from another
    integration has no stored target states to read, so there is nothing to
    import from it.
    """
    component = hass.data.get(scene_component.DATA_COMPONENT)
    if component is None:
        return {}
    return {
        entity.entity_id: entity
        for entity in component.entities
        if getattr(entity, "scene_config", None) is not None
    }


def _split_by_room(
    hass: HomeAssistant, entry: Any, states: dict[str, Any]
) -> tuple[dict[str, list[str]], list[str]]:
    """Which of a scene's lights belong to which room, and which to none."""
    owner: dict[str, str] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SubentryType.ROOM.value:
            continue
        for light in subentry.data.get(CONF_LIGHTS) or ():
            owner[light] = subentry.subentry_id

    by_room: dict[str, list[str]] = {}
    homeless: list[str] = []
    for entity_id in states:
        if not entity_id.startswith("light."):
            # Scenes can set anything; we only know what to do with lights.
            homeless.append(entity_id)
        elif (room_id := owner.get(entity_id)) is not None:
            by_room.setdefault(room_id, []).append(entity_id)
        else:
            homeless.append(entity_id)
    return by_room, homeless


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/importable_scenes"})
@callback
def websocket_importable_scenes(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Home Assistant's scenes, and how each one falls across the rooms."""
    entry = _entry(hass)
    if entry is None:
        connection.send_result(msg["id"], {"scenes": []})
        return

    scenes = []
    for entity_id, entity in _ha_scenes(hass).items():
        states = entity.scene_config.states
        by_room, homeless = _split_by_room(hass, entry, states)
        scenes.append(
            {
                "entity_id": entity_id,
                "name": entity.scene_config.name,
                # Keyed by room so the panel can offer one tick per room: a
                # scene covering the lounge and the kitchen becomes a scene in
                # each, since a scene belongs to exactly one room here.
                "rooms": {
                    room_id: sorted(lights) for room_id, lights in by_room.items()
                },
                "skipped": sorted(homeless),
            }
        )
    connection.send_result(
        msg["id"], {"scenes": sorted(scenes, key=lambda s: s["name"].lower())}
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import_scene",
        vol.Required("entity_id"): str,
        vol.Required("room_ids"): [str],
        vol.Optional("name"): str,
    }
)
@websocket_api.async_response
async def websocket_import_scene(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Copy a Home Assistant scene into one room, or into several.

    Split rather than shared: a scene belongs to exactly one room here, so a
    Home Assistant scene covering three rooms becomes three scenes, each
    holding only the lights that room actually has.
    """
    entry = _entry(hass)
    entity = _ha_scenes(hass).get(msg["entity_id"]) if entry else None
    if entry is None or entity is None:
        connection.send_error(msg["id"], "not_found", "No such scene")
        return

    states = entity.scene_config.states
    by_room, _ = _split_by_room(hass, entry, states)
    created: dict[str, str] = {}

    for room_id in msg["room_ids"]:
        lights = by_room.get(room_id)
        subentry = _room_subentry(entry, room_id)
        if not lights or subentry is None:
            continue
        entries = {
            entity_id: spec
            for entity_id in lights
            if (spec := _scene_entry(states.get(entity_id))) is not None
        }
        scene = {
            CONF_SCENE_ID: ulid_util.ulid_now(),
            CONF_NAME: msg.get("name") or entity.scene_config.name,
            CONF_ICON: entity.scene_config.icon or "mdi:palette",
            CONF_TRANSITION: 1.5,
            CONF_ON_LIGHTS_ONLY: False,
            CONF_IGNORE_PRESENCE: False,
            CONF_OTHERS: "adaptive",
            CONF_ON_UNSUPPORTED_COLOR: "adaptive",
            CONF_SCENE_LIGHTS: entries,
        }
        hass.config_entries.async_update_subentry(
            entry,
            subentry,
            data={
                **subentry.data,
                CONF_ROOM_SCENES: [
                    *(subentry.data.get(CONF_ROOM_SCENES) or ()),
                    scene,
                ],
            },
        )
        created[room_id] = scene[CONF_SCENE_ID]

    connection.send_result(msg["id"], {"created": created})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/version"})
@callback
def websocket_version(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The fingerprint of the script currently on disk.

    A page already open is running whatever it was served, and an upgrade
    cannot reach it: the module is cached under its old URL and nothing tells
    the browser otherwise. Comparing this with the fingerprint the page was
    loaded under is how it finds out, so it can offer the reload rather than
    leaving somebody to wonder why their new settings are missing.
    """
    manifest = json.loads(
        (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
    )
    connection.send_result(
        msg["id"],
        {
            "panel": _fingerprint(),
            "version": manifest.get("version"),
            "name": manifest.get("name"),
            "documentation": manifest.get("documentation"),
            "issues": manifest.get("issue_tracker"),
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/diagnostics"})
@websocket_api.async_response
async def websocket_diagnostics(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What every room and mode currently believes.

    The same dump the diagnostics download produces, rather than a second one
    written for the page: a diagnostic that disagrees with the one attached to
    a bug report is worse than none. What the page adds is that you can watch
    it, next to the events that explain how it got there.
    """
    # Imported here rather than at the top: diagnostics reaches back into the
    # package for its entry type, and this module is loaded while that package
    # is still being defined.
    from .diagnostics import async_get_config_entry_diagnostics

    entry = _entry(hass)
    if entry is None:
        connection.send_result(msg["id"], {})
        return
    connection.send_result(
        msg["id"], await async_get_config_entry_diagnostics(hass, entry)
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/activity",
        vol.Optional("clear", default=False): bool,
    }
)
@callback
def websocket_activity(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What has happened lately, from before this page was opened.

    The bus remembers nothing, so a page opened after the fact used to show
    an empty log and the impression that nothing had happened.
    """
    entry = _entry(hass)
    log = getattr(entry.runtime_data, "activity", None) if entry else None
    if log is None:
        connection.send_result(msg["id"], {"entries": [], "retention_hours": 0})
        return
    if msg["clear"]:
        log.async_clear()
    connection.send_result(
        msg["id"],
        {"entries": log.recent(), "retention_hours": log.retention_hours},
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/curve", vol.Required("room_id"): str}
)
@callback
def websocket_curve(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The adaptive curve for one room, sampled across today.

    The curve is the least visible thing the integration does: a number
    arrives at a bulb and there is no way to tell whether it came from the
    shape you asked for. Sampling it and drawing it turns "the evening feels
    too bright" into something you can point at.

    Computed with the room's own resolved config -- hub defaults, room
    overrides, the lot -- so what is drawn is what the renderer will use.
    """
    entry = _entry(hass)
    controller = entry.runtime_data.controllers.get(msg["room_id"]) if entry else None
    if controller is None:
        connection.send_error(msg["id"], "not_found", "No such room")
        return

    config = controller.adaptive_config()
    now = dt_util.utcnow()
    start = dt_util.start_of_local_day(dt_util.as_local(now)).astimezone(UTC)

    samples = []
    for step in range(_CURVE_STEPS + 1):
        moment = start + timedelta(minutes=step * (1440 // _CURVE_STEPS))
        point = adaptive.compute(config, moment)
        samples.append(
            {
                "at": moment.isoformat(),
                "brightness_pct": round(point.brightness_pct, 1),
                "color_temp_kelvin": point.color_temp_kelvin,
                "sun_position": round(point.sun_position, 4),
            }
        )

    events = {
        "sunrise": config.sun.sunrise(now).isoformat(),
        "sunset": config.sun.sunset(now).isoformat(),
    }

    current = adaptive.compute(config, now, is_night=controller.is_night)
    connection.send_result(
        msg["id"],
        {
            "samples": samples,
            "events": events,
            "now": {
                "at": now.isoformat(),
                "brightness_pct": round(current.brightness_pct, 1),
                "color_temp_kelvin": current.color_temp_kelvin,
                "sun_position": round(current.sun_position, 4),
                "is_night": current.is_night,
            },
            "config": {
                "brightness_mode": config.brightness_mode.value,
                "min_brightness_pct": config.min_brightness_pct,
                "max_brightness_pct": config.max_brightness_pct,
                "min_color_temp_k": config.min_color_temp_k,
                "max_color_temp_k": config.max_color_temp_k,
            },
            # What the renderer would actually send each light right now,
            # after offsets, clamps and anything held manually.
            "lights": [
                {
                    "entity_id": command.entity_id,
                    "brightness": command.data.get("brightness"),
                    "color_temp_kelvin": command.data.get("color_temp_kelvin"),
                    "reason": command.reason,
                }
                for command in controller.commands_for(Trigger.TICK)
            ],
        },
    )
