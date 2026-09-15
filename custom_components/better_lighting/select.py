"""The per-zone mode select: what this room is currently doing."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import BetterLightingConfigEntry
from .const import DOMAIN, IDLE_STATE
from .models import ZoneConfig
from .modes import ModeGroupRuntime
from .render import ZoneMode
from .zone import ZoneController

PARALLEL_UPDATES = 0

OPTION_OFF = "Off"
OPTION_ADAPTIVE = "Adaptive"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the mode select for every zone."""
    runtime = entry.runtime_data
    for subentry_id, zone in runtime.zones.items():
        async_add_entities(
            [ZoneModeSelect(zone, runtime.controllers[subentry_id])],
            config_subentry_id=subentry_id,
        )
    for subentry_id, mode_runtime in runtime.mode_runtimes.items():
        async_add_entities(
            [ModeStateSelect(mode_runtime)], config_subentry_id=subentry_id
        )


class ZoneModeSelect(SelectEntity, RestoreEntity):
    """Shows and sets the zone's mode.

    ``current_option`` is derived from the controller's own state, not by
    matching live light attributes against scene definitions. Scenery has to
    guess because it owns no state of its own; we own the state machine, so
    guessing would be strictly worse -- it would flip whenever a member drifted,
    and it cannot tell "adaptive, which happens to be 2700 K right now" from
    "a scene that is 2700 K".
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "zone_mode"
    _attr_icon = "mdi:palette-outline"

    def __init__(self, zone: ZoneConfig, controller: ZoneController) -> None:
        self.zone = zone
        self.controller = controller
        self._attr_unique_id = f"{zone.subentry_id}_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, zone.subentry_id)},
            name=zone.name,
            manufacturer="Better Lighting",
            model="Zone",
            entry_type=DeviceEntryType.SERVICE,
        )

    # -- options -----------------------------------------------------------

    @property
    def _scene_names(self) -> dict[str, str]:
        """Display name -> scene id, with collisions made unambiguous."""
        names: dict[str, str] = {}
        for scene_id, scene in self.controller.scenes.items():
            name = scene.name or scene_id
            if name in (OPTION_OFF, OPTION_ADAPTIVE) or name in names:
                # A scene called "Off" would otherwise shadow the real option.
                name = f"{name} ({scene_id[:6]})"
            names[name] = scene_id
        return names

    @property
    def options(self) -> list[str]:
        return [OPTION_OFF, OPTION_ADAPTIVE, *self._scene_names]

    @property
    def current_option(self) -> str | None:
        mode = self.controller.mode
        if mode is ZoneMode.OFF:
            return OPTION_OFF
        if mode is ZoneMode.SCENE:
            for name, scene_id in self._scene_names.items():
                if scene_id == self.controller.active_scene_id:
                    return name
            return None
        return OPTION_ADAPTIVE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        controller = self.controller
        return {
            "bl_mode": controller.mode.value,
            # The mode as actually rendered, with night folded in.
            "bl_effective_mode": controller.effective_mode.value,
            "bl_scene_id": controller.active_scene_id,
            "bl_night": controller.night_active,
            "bl_adaptive_enabled": controller.adaptive_enabled,
            "bl_bias_pct": controller.bias_pct,
            "bl_saturated": {
                entity_id: str(flags)
                for entity_id, flags in controller.saturated_lights.items()
            },
        }

    # -- lifecycle ---------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.controller.async_add_listener(self._handle_update))

        # Restore the mode, so a room the user left in "Cooking" is still in
        # Cooking after a restart rather than silently back to adaptive.
        if (last := await self.async_get_last_state()) is not None:
            await self._async_apply(last.state, render=False)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    # -- commands ----------------------------------------------------------

    async def async_select_option(self, option: str) -> None:
        await self._async_apply(option, render=True)

    async def _async_apply(self, option: str, *, render: bool) -> None:
        if option == OPTION_OFF:
            target, scene_id = ZoneMode.OFF, None
        elif option == OPTION_ADAPTIVE:
            target, scene_id = ZoneMode.ADAPTIVE, None
        elif (scene_id := self._scene_names.get(option)) is not None:
            target = ZoneMode.SCENE
        else:
            # A restored option naming a scene that has since been deleted.
            return

        if render:
            await self.controller.async_set_mode(target, scene_id)
        else:
            self.controller.mode = target
            self.controller.active_scene_id = scene_id
            self.async_write_ha_state()


class ModeStateSelect(SelectEntity, RestoreEntity):
    """The state of a cross-zone mode.

    This is the integration point for an external automation: a media player
    template calls ``select.select_option`` with playing, paused or credits,
    and everything else follows from the rules.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "mode_state"

    def __init__(self, mode_runtime: ModeGroupRuntime) -> None:
        self.runtime = mode_runtime
        config = mode_runtime.config
        self._attr_unique_id = f"{config.subentry_id}_state"
        self._attr_icon = config.icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config.subentry_id)},
            name=config.name,
            manufacturer="Better Lighting",
            model="Mode",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def options(self) -> list[str]:
        return [IDLE_STATE, *self.runtime.config.states]

    @property
    def current_option(self) -> str:
        return self.runtime.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        runtime = self.runtime
        return {
            "bl_session_id": runtime.session_id,
            "bl_zones": sorted(runtime.config.zone_ids),
            "bl_opted_out": sorted(runtime.opted_out),
            "bl_deferred": sorted(
                action.zone_id
                for action in runtime.deferred
                if action.session_id == runtime.session_id
            ),
            "bl_snapshot_taken_at": (
                runtime.snapshot.taken_at if runtime.snapshot else None
            ),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.async_add_listener(self._handle_update))
        # No restore from the entity's own state here: the session file is
        # authoritative and has already been read, and re-applying from the
        # entity would replay the mode rather than reconcile with it.

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        await self.runtime.async_set_state(option)
