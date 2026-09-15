"""The zone light entity: a light group with relative dimming and on-state memory."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from math import atan2, cos, degrees, radians, sin
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_HS_COLOR,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    ATTR_WHITE,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
    filter_supported_color_modes,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import (
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from . import BetterLightingConfigEntry
from .brightness import (
    group_by_brightness,
    relative_brightness_map,
    representative_brightness,
)
from .const import DOMAIN
from .context import ContextRegistry
from .group_entity import GroupEntity
from .models import HubConfig, ZoneConfig
from .zone import Trigger, ZoneController

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Colour attributes a caller may set on the group.
COLOR_ATTRS = (
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_XY_COLOR,
    ATTR_WHITE,
)
# Attributes that mean "the caller specified a look", as opposed to a bare
# turn-on. A bare turn-on is a switch press; anything here is a manual override.
VISUAL_ATTRS = (ATTR_BRIGHTNESS, ATTR_EFFECT, *COLOR_ATTRS)

VALID_MEMBER_STATES = (STATE_ON, "off")

DEFAULT_MIN_KELVIN = 2000
DEFAULT_MAX_KELVIN = 6535


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one light entity per zone subentry."""
    runtime = entry.runtime_data
    for subentry_id, zone in runtime.zones.items():
        entity = ZoneLight(
            zone,
            runtime.hub,
            runtime.controllers[subentry_id],
            runtime.contexts,
        )
        runtime.zone_lights[subentry_id] = entity
        # Binding to the subentry gives the zone its own device, and lets HA
        # clean both up automatically when the subentry is deleted.
        async_add_entities([entity], config_subentry_id=subentry_id)


def _mean_tuple(values: list[tuple[float, ...]]) -> tuple[float, ...]:
    """Component-wise mean of equal-length tuples."""
    return tuple(
        sum(component) / len(component) for component in zip(*values, strict=True)
    )


def _mean_circle(values: list[tuple[float, float]]) -> tuple[float, float]:
    """Mean of (hue, saturation) pairs, averaging hue on the circle.

    A plain arithmetic mean of hues 350 and 10 gives 180 -- cyan, the opposite
    of the red both inputs describe. Averaging the unit vectors instead gives 0.
    """
    hues, saturations = zip(*values, strict=True)
    x = sum(cos(radians(h)) for h in hues)
    y = sum(sin(radians(h)) for h in hues)
    return (degrees(atan2(y, x)) % 360, sum(saturations) / len(saturations))


def _reduce_attribute(
    states: list[State],
    attribute: str,
    reducer: Any = None,
) -> Any:
    """Collect one attribute across states and reduce it, or None if absent."""
    values = [
        value
        for state in states
        if (value := state.attributes.get(attribute)) is not None
    ]
    if not values:
        return None
    if reducer is None:
        return int(sum(values) / len(values))
    return reducer(values)


class ZoneLightExtraData(ExtraStoredData):
    """The on-state memory, persisted so a restart does not lose it."""

    def __init__(
        self, remembered: list[str] | None, brightness: dict[str, int]
    ) -> None:
        self.remembered = remembered
        self.brightness = brightness

    def as_dict(self) -> dict[str, Any]:
        return {"remembered": self.remembered, "brightness": self.brightness}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ZoneLightExtraData:
        remembered = data.get("remembered")
        brightness = data.get("brightness") or {}
        return cls(
            list(remembered) if remembered is not None else None,
            {str(k): int(v) for k, v in brightness.items()},
        )


class ZoneLight(GroupEntity, LightEntity, RestoreEntity):
    """A zone's lights, presented as one light.

    Invariant that the rest of the integration depends on: **the render engine
    never calls a service on this entity**, only on its members. Every call that
    arrives at :meth:`async_turn_on` is therefore external by construction --
    a wall switch, a dashboard, a voice assistant or an automation -- which is
    what lets us tell a switch press from our own output without heuristics.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(
        self,
        zone: ZoneConfig,
        hub: HubConfig,
        controller: ZoneController,
        contexts: ContextRegistry,
    ) -> None:
        self.zone = zone
        self.hub = hub
        self.controller = controller
        self.contexts = contexts
        self._entity_ids = list(zone.lights)

        self._attr_unique_id = f"{zone.subentry_id}_light"
        self._attr_icon = zone.icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, zone.subentry_id)},
            name=zone.name,
            manufacturer="Better Lighting",
            model="Zone",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._attr_available = False
        self._attr_is_on = False
        # HA reads capability_attributes when the entity is registered, which is
        # before any member state is known. Core's LightGroup seeds the same two
        # placeholders; the first group-state update replaces them.
        self._attr_color_mode = ColorMode.UNKNOWN
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_supported_features = LightEntityFeature(0)
        self._attr_min_color_temp_kelvin = DEFAULT_MIN_KELVIN
        self._attr_max_color_temp_kelvin = DEFAULT_MAX_KELVIN

        # Which members were on when this zone was last turned off, so that
        # turning it back on restores the same set rather than lighting the
        # whole room. `None` means "no trustworthy memory, use all members".
        self._remembered: list[str] | None = None
        self._remembered_brightness: dict[str, int] = {}

    # -- lifecycle ---------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Restore the on-state memory, then start tracking members."""
        if (stored := await self.async_get_last_extra_data()) is not None:
            data = ZoneLightExtraData.from_dict(stored.as_dict())
            self._remembered = data.remembered
            self._remembered_brightness = data.brightness
            _LOGGER.debug(
                "%s restored on-state memory: %s", self.entity_id, self._remembered
            )
        await super().async_added_to_hass()

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return ZoneLightExtraData(self._remembered, self._remembered_brightness)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_ENTITY_ID: self._entity_ids,
            "bl_zone_id": self.zone.subentry_id,
            "bl_remembered_members": self._remembered,
        }

    # -- member bookkeeping ------------------------------------------------

    def _member_states(self) -> list[State]:
        """Live states of every member that currently reports one."""
        return [
            state
            for entity_id in self._entity_ids
            if (state := self.hass.states.get(entity_id)) is not None
        ]

    @staticmethod
    def _valid(states: Iterable[State]) -> list[State]:
        """Members in a usable state. Unavailable members must not skew a mean."""
        return [s for s in states if s.state in VALID_MEMBER_STATES]

    @staticmethod
    def _on(states: Iterable[State]) -> list[State]:
        return [s for s in states if s.state == STATE_ON]

    def is_our_context(self, context: Context | None) -> bool:
        """Did we cause this?

        Shared with the zone controller, so a command issued by either is
        recognised by both. Keeping two separate registries would mean the
        group treating the controller's renders as external user activity.
        """
        return self.contexts.is_ours(context)

    @callback
    def async_should_defer_state_change(
        self, event: Event[EventStateChangedData]
    ) -> bool:
        """Members re-reporting our own command carry no new information."""
        return False  # M4 will defer echoes here; for now every update counts.

    # -- aggregation -------------------------------------------------------

    @callback
    def async_update_group_state(self) -> bool:
        """Recompute this zone's state from its members."""
        states = self._member_states()
        valid = self._valid(states)
        on_states = self._on(valid)

        if not valid:
            # Every member missing or unavailable: so is the zone.
            self._attr_available = any(
                s.state != STATE_UNAVAILABLE for s in states
            ) and bool(states)
            self._attr_is_on = False
            return True

        self._attr_available = True
        mode = all if self.zone.all_members_on else any
        self._attr_is_on = mode(s.state == STATE_ON for s in valid)

        # Brightness comes from the members that are actually on; including an
        # off member would drag the reported value toward zero.
        self._attr_brightness = representative_brightness(
            {s.entity_id: s.attributes.get(ATTR_BRIGHTNESS) for s in on_states},
            self.zone.brightness_strategy,
        )

        self._attr_color_temp_kelvin = _reduce_attribute(
            on_states, ATTR_COLOR_TEMP_KELVIN
        )
        self._attr_min_color_temp_kelvin = (
            _reduce_attribute(valid, ATTR_MIN_COLOR_TEMP_KELVIN, min)
            or DEFAULT_MIN_KELVIN
        )
        self._attr_max_color_temp_kelvin = (
            _reduce_attribute(valid, ATTR_MAX_COLOR_TEMP_KELVIN, max)
            or DEFAULT_MAX_KELVIN
        )
        self._attr_hs_color = _reduce_attribute(on_states, ATTR_HS_COLOR, _mean_circle)
        self._attr_rgb_color = _reduce_attribute(on_states, ATTR_RGB_COLOR, _mean_tuple)
        self._attr_rgbw_color = _reduce_attribute(
            on_states, ATTR_RGBW_COLOR, _mean_tuple
        )
        self._attr_rgbww_color = _reduce_attribute(
            on_states, ATTR_RGBWW_COLOR, _mean_tuple
        )
        self._attr_xy_color = _reduce_attribute(on_states, ATTR_XY_COLOR, _mean_tuple)

        self._update_color_modes(valid, on_states)
        self._update_effects(valid, on_states)
        self._update_supported_features(valid)
        self._update_assumed_state_from_members()
        return True

    @callback
    def _update_color_modes(self, valid: list[State], on_states: list[State]) -> None:
        modes: set[str] = set()
        for state in valid:
            modes.update(state.attributes.get(ATTR_SUPPORTED_COLOR_MODES) or [])
        self._attr_supported_color_modes = filter_supported_color_modes(modes) or {
            ColorMode.ONOFF
        }

        current = [
            mode
            for state in on_states
            if (mode := state.attributes.get(ATTR_COLOR_MODE)) is not None
        ]
        if current:
            # Prefer the richest mode in use: a single ONOFF member must not
            # make a colour-capable zone report as a plain switch.
            def rank(mode: str) -> tuple[int, int]:
                weight = {ColorMode.ONOFF: -1, ColorMode.BRIGHTNESS: 0}.get(mode, 1)
                return (weight, current.count(mode))

            self._attr_color_mode = max(set(current), key=rank)
        elif self._attr_supported_color_modes:
            self._attr_color_mode = next(iter(self._attr_supported_color_modes))
        else:
            self._attr_color_mode = ColorMode.UNKNOWN

    @callback
    def _update_effects(self, valid: list[State], on_states: list[State]) -> None:
        effects: set[str] = set()
        for state in valid:
            effects.update(state.attributes.get(ATTR_EFFECT_LIST) or [])
        self._attr_effect_list = sorted(effects) if effects else None

        active = [
            effect
            for state in on_states
            if (effect := state.attributes.get(ATTR_EFFECT)) is not None
        ]
        self._attr_effect = max(set(active), key=active.count) if active else None

    @callback
    def _update_supported_features(self, valid: list[State]) -> None:
        features = 0
        for state in valid:
            features |= state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0
        # Only the features that are meaningful for a group; per-member quirks
        # such as SUPPORT_WHITE_VALUE are handled when we build the payload.
        self._attr_supported_features = LightEntityFeature(features) & (
            LightEntityFeature.EFFECT
            | LightEntityFeature.FLASH
            | LightEntityFeature.TRANSITION
        )

    # -- commands ----------------------------------------------------------

    async def _async_call_members(
        self, service: str, data: dict[str, Any], entity_ids: list[str]
    ) -> None:
        """Issue one service call to a set of members, tagged as ours."""
        if not entity_ids:
            return
        context = self.contexts.new_context(
            self.zone.subentry_id, f"group_{service}", parent=self._context
        )
        for entity_id in entity_ids:
            self.contexts.note_command(entity_id)
        await self.hass.services.async_call(
            LIGHT_DOMAIN,
            service,
            {**data, ATTR_ENTITY_ID: entity_ids},
            blocking=True,
            context=context,
        )

    def _restore_targets(self) -> list[str]:
        """Which members a bare turn-on should light."""
        if not self.zone.remember_on_state or self._remembered is None:
            return list(self._entity_ids)
        # Membership may have changed since the memory was taken; if it no
        # longer lines up, fall back to the whole zone rather than guessing.
        allowed = set(self._entity_ids)
        remembered = [e for e in self._remembered if e in allowed]
        return remembered or list(self._entity_ids)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Handle an external turn-on.

        M1 handles the three shapes directly. M4 routes the bare case through
        the zone state machine so it becomes a controller press.
        """
        if self.is_our_context(self._context):
            _LOGGER.warning(
                "%s received a turn_on in our own context; ignoring to avoid a "
                "feedback loop. This usually means an automation or an area "
                "target includes both this zone and its members.",
                self.entity_id,
            )
            return

        transition = kwargs.get(ATTR_TRANSITION)
        base: dict[str, Any] = {}
        if transition is not None:
            base[ATTR_TRANSITION] = transition

        colour = {k: v for k, v in kwargs.items() if k in COLOR_ATTRS}
        effect = {ATTR_EFFECT: kwargs[ATTR_EFFECT]} if ATTR_EFFECT in kwargs else {}
        visual = {**colour, **effect}

        if ATTR_BRIGHTNESS in kwargs and self.is_on:
            await self._async_relative_dim(kwargs[ATTR_BRIGHTNESS], base | visual)
            return

        if not self.is_on:
            # A bare turn-on restores the remembered members; an explicit
            # brightness or colour applies to all of them.
            targets = self._restore_targets()
            data = base | visual
            if ATTR_BRIGHTNESS in kwargs:
                data[ATTR_BRIGHTNESS] = kwargs[ATTR_BRIGHTNESS]
            # Nothing was asked for, so light them at the value the curve says
            # they should already be. Doing this in one command is what avoids
            # the flash-then-correct of a two-step turn-on -- and it needs no
            # patching of Home Assistant internals, because this method runs
            # before any member is touched.
            if (
                not data
                and self.controller.adaptive_enabled
                and await self._async_turn_on_adaptive(targets)
            ):
                return
            await self._async_call_members(SERVICE_TURN_ON, data, targets)
            return

        # Already on: a visual change applies only to the members that are lit,
        # so a colour tweak does not switch the rest of the room on.
        on_ids = [s.entity_id for s in self._on(self._valid(self._member_states()))]
        await self._async_call_members(
            SERVICE_TURN_ON, base | visual, on_ids or list(self._entity_ids)
        )

    async def _async_turn_on_adaptive(self, targets: list[str]) -> bool:
        """Light ``targets`` at their adaptive values. False if nothing to send."""
        resolved = self.controller.targets_for(targets, trigger=Trigger.TURN_ON)
        if not resolved:
            return False

        transition = self.hub.initial_transition
        batched: dict[tuple, list[str]] = {}
        for target in resolved:
            data = target.as_service_data()
            if transition:
                data[ATTR_TRANSITION] = transition
            key = tuple(sorted((k, _hashable(v)) for k, v in data.items()))
            batched.setdefault(key, []).append(target.entity_id)

        # Any member without a resolvable target still needs switching on.
        covered = {target.entity_id for target in resolved}
        if remainder := [eid for eid in targets if eid not in covered]:
            await self._async_call_members(SERVICE_TURN_ON, {}, remainder)

        for key, entity_ids in batched.items():
            await self._async_call_members(SERVICE_TURN_ON, dict(key), entity_ids)
        return True

    async def _async_relative_dim(
        self, target_brightness: int, extra: dict[str, Any]
    ) -> None:
        """Move every lit member by the same fraction of its own headroom."""
        on_states = self._on(self._valid(self._member_states()))
        current = self._attr_brightness
        if current is None or not on_states:
            return

        mapping = relative_brightness_map(
            {s.entity_id: s.attributes.get(ATTR_BRIGHTNESS) for s in on_states},
            current,
            int(target_brightness),
        )
        if not mapping:
            return

        # Members landing on the same value share one service call.
        for brightness, entity_ids in group_by_brightness(mapping).items():
            await self._async_call_members(
                SERVICE_TURN_ON, {**extra, ATTR_BRIGHTNESS: brightness}, entity_ids
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Remember which members were on, then turn the whole zone off."""
        states = self._member_states()
        valid = self._valid(states)
        on_states = self._on(valid)

        if self.zone.remember_on_state:
            # Snapshot before issuing anything: once the members start turning
            # off, the information is gone.
            self._remembered = [s.entity_id for s in on_states] if valid else None
            self._remembered_brightness = {
                s.entity_id: int(brightness)
                for s in on_states
                if (brightness := s.attributes.get(ATTR_BRIGHTNESS)) is not None
            }

        data: dict[str, Any] = {}
        if (transition := kwargs.get(ATTR_TRANSITION)) is not None:
            data[ATTR_TRANSITION] = transition
        await self._async_call_members(SERVICE_TURN_OFF, data, list(self._entity_ids))


def _hashable(value: Any) -> Any:
    """Make a service-data value usable as part of a dict key."""
    return tuple(value) if isinstance(value, list | tuple) else value
