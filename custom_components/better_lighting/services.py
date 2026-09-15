"""Services, so automations can drive a zone directly.

Zones are addressable two ways, and both work everywhere: by Home Assistant
``target`` (any of the zone's own entities, which is what the UI service picker
produces), or by ``zone`` naming the zone's slug or subentry id (stable across
renaming an entity, which is what a documented automation should use).
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .render import ZoneMode
from .zone import Trigger, ZoneController

_LOGGER = logging.getLogger(__name__)

SERVICE_PRESS = "press"
SERVICE_CYCLE = "cycle"
SERVICE_SET_ADAPTIVE = "set_adaptive"
SERVICE_ACTIVATE_SCENE = "activate_scene"
SERVICE_CLEAR_MANUAL = "clear_manual_override"
SERVICE_SET_MODE = "set_mode"
SERVICE_END_MODE = "end_mode"
SERVICE_REJOIN_MODE = "rejoin_mode"

ATTR_ZONE = "zone"
ATTR_CONTROLLER = "controller"
ATTR_KIND = "kind"
ATTR_DIRECTION = "direction"
ATTR_SCENE = "scene"
ATTR_MODE = "mode"
ATTR_STATE = "state"
ATTR_RESTORE = "restore"

_TARGET = {
    vol.Optional(ATTR_ZONE): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_ENTITY_ID): cv.comp_entity_ids,
}

PRESS_SCHEMA = vol.Schema(
    {
        **_TARGET,
        vol.Optional(ATTR_CONTROLLER): cv.string,
        vol.Optional(ATTR_KIND, default="press"): vol.In(
            ["press", "double_press", "long_press"]
        ),
    }
)
CYCLE_SCHEMA = vol.Schema(
    {
        **_TARGET,
        vol.Optional(ATTR_CONTROLLER): cv.string,
        vol.Optional(ATTR_DIRECTION, default="next"): vol.In(["next", "previous"]),
    }
)
SET_ADAPTIVE_SCHEMA = vol.Schema(_TARGET)
ACTIVATE_SCENE_SCHEMA = vol.Schema({**_TARGET, vol.Required(ATTR_SCENE): cv.string})
SET_MODE_SCHEMA = vol.Schema(
    {vol.Required(ATTR_MODE): cv.string, vol.Required(ATTR_STATE): cv.string}
)
END_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MODE): cv.string,
        vol.Optional(ATTR_RESTORE, default=True): cv.boolean,
    }
)
REJOIN_MODE_SCHEMA = vol.Schema({**_TARGET, vol.Required(ATTR_MODE): cv.string})

CLEAR_MANUAL_SCHEMA = vol.Schema(
    {**_TARGET, vol.Optional("lights"): vol.All(cv.ensure_list, [cv.entity_id])}
)


def _runtimes(hass: HomeAssistant) -> list[Any]:
    """Every loaded Better Lighting entry's runtime."""
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def _zone_ids_from_entities(hass: HomeAssistant, entity_ids: list[str]) -> set[str]:
    """Map any of our entities back to the zone subentry that owns it."""
    registry = er.async_get(hass)
    zone_ids: set[str] = set()
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        if entry is not None and entry.platform == DOMAIN and entry.config_subentry_id:
            zone_ids.add(entry.config_subentry_id)
    return zone_ids


def resolve_controllers(hass: HomeAssistant, call: ServiceCall) -> list[ZoneController]:
    """The zone controllers a call is aimed at."""
    wanted_ids = set(call.data.get(ATTR_ZONE) or ())
    entity_ids = call.data.get(ATTR_ENTITY_ID) or []
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]
    wanted_ids |= _zone_ids_from_entities(hass, entity_ids)

    if not wanted_ids:
        raise ServiceValidationError(
            "No zone was targeted. Pass a zone entity as the target, or name one "
            "with the 'zone' field."
        )

    found: list[ZoneController] = []
    for runtime in _runtimes(hass):
        for subentry_id, controller in runtime.controllers.items():
            zone = runtime.zones[subentry_id]
            if subentry_id in wanted_ids or zone.slug in wanted_ids:
                found.append(controller)

    if not found:
        raise ServiceValidationError(
            f"No Better Lighting zone matches {sorted(wanted_ids)}."
        )
    return found


def _switch_for(hass: HomeAssistant, controller: ZoneController, named: str | None):
    """Which controller's list to use: the named one, or the zone's default."""
    for runtime in _runtimes(hass):
        if named:
            for switch in runtime.switches.values():
                if named in (switch.subentry_id, switch.slug, switch.name):
                    return switch
        default = runtime.default_switch.get(controller.zone.subentry_id)
        if default is not None:
            return default
    return None


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services, once per Home Assistant."""
    if hass.services.has_service(DOMAIN, SERVICE_PRESS):
        return

    async def _press(call: ServiceCall) -> None:
        named = call.data.get(ATTR_CONTROLLER)
        for controller in resolve_controllers(hass, call):
            switch = _switch_for(hass, controller, named)
            if switch is None:
                continue
            await controller.async_press(switch, call.data[ATTR_KIND])

    async def _cycle(call: ServiceCall) -> None:
        named = call.data.get(ATTR_CONTROLLER)
        direction = 1 if call.data[ATTR_DIRECTION] == "next" else -1
        for controller in resolve_controllers(hass, call):
            switch = _switch_for(hass, controller, named)
            if switch is None:
                continue
            await controller.async_cycle(switch, direction=direction)

    async def _set_adaptive(call: ServiceCall) -> None:
        for controller in resolve_controllers(hass, call):
            # Requirement 1: force a zone back to adaptive from a script,
            # whatever it happened to be doing.
            controller.clear_manual()
            await controller.async_set_adaptive()

    async def _activate_scene(call: ServiceCall) -> None:
        wanted = call.data[ATTR_SCENE]
        for controller in resolve_controllers(hass, call):
            scene_id = next(
                (
                    candidate
                    for candidate, scene in controller.scenes.items()
                    if wanted in (candidate, scene.name)
                ),
                None,
            )
            if scene_id is None:
                raise ServiceValidationError(
                    f"{controller.zone.name} has no scene called {wanted!r}."
                )
            await controller.async_set_mode(ZoneMode.SCENE, scene_id)

    async def _clear_manual(call: ServiceCall) -> None:
        lights = call.data.get("lights")
        for controller in resolve_controllers(hass, call):
            if lights:
                for entity_id in lights:
                    controller.clear_manual(entity_id)
            else:
                controller.clear_manual()
            await controller.async_render(Trigger.ACTIVATE)

    def _find_mode(named: str):
        for runtime in _runtimes(hass):
            for mode_runtime in runtime.mode_runtimes.values():
                config = mode_runtime.config
                if named in (config.subentry_id, config.slug, config.name):
                    return mode_runtime
        raise ServiceValidationError(f"No Better Lighting mode called {named!r}.")

    async def _set_mode(call: ServiceCall) -> None:
        mode_runtime = _find_mode(call.data[ATTR_MODE])
        await mode_runtime.async_set_state(call.data[ATTR_STATE])

    async def _end_mode(call: ServiceCall) -> None:
        mode_runtime = _find_mode(call.data[ATTR_MODE])
        await mode_runtime.async_end(restore=call.data[ATTR_RESTORE])

    async def _rejoin_mode(call: ServiceCall) -> None:
        mode_runtime = _find_mode(call.data[ATTR_MODE])
        for controller in resolve_controllers(hass, call):
            zone_id = controller.zone.subentry_id
            mode_runtime.opted_out.discard(zone_id)
            if mode_runtime.snapshot and zone_id in mode_runtime.snapshot.zones:
                mode_runtime.snapshot.zones[zone_id].restore_on_exit = True
            rule = mode_runtime.config.rule_for(mode_runtime.state, zone_id)
            if mode_runtime.active and rule is not None:
                await mode_runtime._async_apply_rule(zone_id, controller, rule)
        mode_runtime.async_notify()

    hass.services.async_register(DOMAIN, SERVICE_SET_MODE, _set_mode, SET_MODE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_END_MODE, _end_mode, END_MODE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REJOIN_MODE, _rejoin_mode, REJOIN_MODE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_PRESS, _press, PRESS_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CYCLE, _cycle, CYCLE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_ADAPTIVE, _set_adaptive, SET_ADAPTIVE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ACTIVATE_SCENE, _activate_scene, ACTIVATE_SCENE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_MANUAL, _clear_manual, CLEAR_MANUAL_SCHEMA
    )
