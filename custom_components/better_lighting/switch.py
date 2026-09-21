"""The per-room adaptive and night-mode switches, and the mode on/off switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import BetterLightingConfigEntry
from .const import DOMAIN, NightBehavior
from .models import RoomConfig
from .modes import ModeGroupRuntime
from .profiles import Axis
from .room import RoomController

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the adaptive and night switches per room, and one per mode."""
    runtime = entry.runtime_data
    for subentry_id, room in runtime.rooms.items():
        controller = runtime.controllers[subentry_id]
        async_add_entities(
            [
                AdaptiveAxisSwitch(room, controller, Axis.BRIGHTNESS),
                AdaptiveAxisSwitch(room, controller, Axis.COLOR),
                NightSwitch(room, controller),
            ],
            config_subentry_id=subentry_id,
        )
    for subentry_id, mode_runtime in runtime.mode_runtimes.items():
        async_add_entities(
            [ModeEnabledSwitch(mode_runtime)], config_subentry_id=subentry_id
        )
    if runtime.simulation is not None:
        async_add_entities([SimulationSwitch(entry, runtime.simulation)])


class _RoomSwitch(SwitchEntity, RestoreEntity):
    """Shared plumbing: device binding and following the controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, room: RoomConfig, controller: RoomController, key: str) -> None:
        self.room = room
        self.controller = controller
        self._attr_unique_id = f"{room.subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room.subentry_id)},
            name=room.name,
            manufacturer="Better Lighting",
            model="Room",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.controller.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class AdaptiveAxisSwitch(_RoomSwitch):
    """Whether this room tracks the sun on one axis.

    Two switches rather than one, because the axes are genuinely independent:
    a room can keep warming through the evening while its brightness stays
    where somebody put it, or hold a colour while still dimming with the day.
    Switching an axis off means "stop following the sun" -- a scene can still
    set that axis.
    """

    def __init__(
        self, room: RoomConfig, controller: RoomController, axis: Axis
    ) -> None:
        self.axis = axis
        key = "adaptive_brightness" if axis is Axis.BRIGHTNESS else "adaptive_color"
        super().__init__(room, controller, key)
        self._attr_icon = (
            "mdi:brightness-auto" if axis is Axis.BRIGHTNESS else "mdi:palette-outline"
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Survive a restart: a room the user took off the curve should not
        # quietly go back on it when Home Assistant restarts.
        if (last := await self.async_get_last_state()) is not None:
            await self.controller.async_set_adaptive_axis(self.axis, last.state == "on")

    @property
    def is_on(self) -> bool:
        if self.axis is Axis.BRIGHTNESS:
            return self.controller.adapt_brightness
        return self.controller.adapt_color

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_adaptive_axis(self.axis, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_adaptive_axis(self.axis, False)


class NightSwitch(_RoomSwitch):
    """Whether this room is in night mode.

    When the room names a source entity this mirrors it, and the source wins on
    every change -- so the user keeps one source of truth for "the house is
    asleep" and this switch is a readout that can still be nudged by hand until
    the source next changes.
    """

    _attr_icon = "mdi:sleep"

    def __init__(self, room: RoomConfig, controller: RoomController) -> None:
        super().__init__(room, controller, "night")

    @property
    def is_on(self) -> bool:
        return self.controller.night_active

    @property
    def available(self) -> bool:
        return self.room.night_behavior is not NightBehavior.OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "source_entity": self.room.night_source_entity,
            "behavior": self.room.night_behavior.value,
            "ignores_presence": self.room.night_ignore_presence,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_night(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_night(False)


class ModeEnabledSwitch(SwitchEntity, RestoreEntity):
    """Whether a mode is allowed to act on the rooms.

    The driving automation keeps reporting the state either way -- so this is
    not a mute button on the automation but on us. That distinction matters
    because automations fire on *changes*: were the state not tracked while the
    mode is switched off, turning it back on halfway through a film would find
    an idle mode and sit there until the film ended.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "mode_enabled"
    _attr_icon = "mdi:motion-play-outline"

    def __init__(self, mode_runtime: ModeGroupRuntime) -> None:
        self.runtime = mode_runtime
        config = mode_runtime.config
        self._attr_unique_id = f"{config.subentry_id}_enabled"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config.subentry_id)},
            name=config.name,
            manufacturer="Better Lighting",
            model="Mode",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.async_add_listener(self._handle_update))
        # A mode the user switched off should stay off across a restart. The
        # session file cannot carry this: it is dropped precisely when the mode
        # is idle, which is the usual case for a mode that is switched off.
        if (last := await self.async_get_last_state()) is not None:
            await self.runtime.async_set_enabled(last.state == "on")

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.runtime.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "bl_tracked_state": self.runtime.state,
            "bl_session_id": self.runtime.session_id,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.runtime.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.runtime.async_set_enabled(False)


class SimulationSwitch(SwitchEntity):
    """Whether the house is currently pretending somebody is in.

    Readable, and writable -- turning it on runs a simulation whether or not
    the away helper agrees, which is how anybody sanely checks that theirs is
    set up before going on holiday to find out. It is not restored across a
    restart: whether to simulate is a question about the house right now, and
    the away helper answers it.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "presence_simulation"
    _attr_icon = "mdi:home-account"

    def __init__(self, entry: BetterLightingConfigEntry, runner: Any) -> None:
        self.runner = runner
        self._attr_unique_id = f"{entry.entry_id}_presence_simulation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Better Lighting",
            manufacturer="Better Lighting",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.runner.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.runner.running

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"rooms": sorted(self.runner.rooms_running)}

    async def async_turn_on(self, **_kwargs: Any) -> None:
        await self.runner.async_start(reason="switched on", forced=True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self.runner.async_stop(reason="switched off")
