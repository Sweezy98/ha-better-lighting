"""The Better Lighting integration.

A single hub config entry owns every configuration object as a subentry, so
scenes are reusable across zones and a cross-zone mode has one place to live.
All runtime state hangs off ``entry.runtime_data`` -- there is deliberately no
module-level singleton, because Adaptive Lighting's shared manager is the root
cause of its per-light cross-talk between profiles.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS, SubentryType
from .context import ContextRegistry
from .models import HubConfig, LightProfileConfig, ZoneConfig
from .profiles import LightProfile
from .zone import ZoneController

if TYPE_CHECKING:
    from .light import ZoneLight

_LOGGER = logging.getLogger(__name__)

type BetterLightingConfigEntry = ConfigEntry[BetterLightingRuntime]


@dataclass(slots=True)
class BetterLightingRuntime:
    """Everything the integration knows, for the lifetime of one entry load."""

    hub: HubConfig
    zones: dict[str, ZoneConfig]
    # Per-light calibration, keyed by light entity_id. A light belongs to one
    # zone, so one profile per light is unambiguous.
    profiles: dict[str, LightProfile] = field(default_factory=dict)
    # One controller per zone; the only thing that commands member lights.
    controllers: dict[str, ZoneController] = field(default_factory=dict)
    # Live entity objects, registered as their platforms come up. Keyed by
    # zone subentry_id so any subsystem can reach a zone without a global.
    zone_lights: dict[str, ZoneLight] = field(default_factory=dict)
    contexts: ContextRegistry = field(default_factory=ContextRegistry)
    # Fingerprint of the config this runtime was built from, so an update
    # callback that changes nothing does not trigger a reload storm.
    config_fingerprint: int = 0


def _fingerprint(entry: ConfigEntry) -> int:
    """A cheap hash of everything a reload would rebuild.

    Config values contain lists (a zone's lights), so this serialises to sorted
    JSON rather than hashing the structure directly.
    """
    payload = {
        "options": dict(entry.options or {}),
        "subentries": sorted(
            (
                {
                    "id": sub.subentry_id,
                    "type": sub.subentry_type,
                    "title": sub.title,
                    "data": dict(sub.data),
                }
                for sub in entry.subentries.values()
            ),
            key=lambda sub: sub["id"],
        ),
    }
    return hash(json.dumps(payload, sort_keys=True, default=str))


def build_runtime(entry: ConfigEntry) -> BetterLightingRuntime:
    """Parse the entry and its subentries into typed config objects."""
    zones = {
        subentry.subentry_id: ZoneConfig.from_subentry(subentry)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SubentryType.ZONE.value
    }
    profiles = {
        profile.light_entity: profile.profile
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SubentryType.LIGHT_PROFILE.value
        and (profile := LightProfileConfig.from_subentry(subentry)).light_entity
    }
    return BetterLightingRuntime(
        hub=HubConfig.from_options(dict(entry.options)),
        zones=zones,
        profiles=profiles,
        config_fingerprint=_fingerprint(entry),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: BetterLightingConfigEntry
) -> bool:
    """Set up the hub entry."""
    runtime = build_runtime(entry)
    entry.runtime_data = runtime
    _LOGGER.debug(
        "Setting up with %d zone(s) and %d light profile(s)",
        len(runtime.zones),
        len(runtime.profiles),
    )

    for subentry_id, zone in runtime.zones.items():
        controller = ZoneController(
            hass, zone, runtime.hub, runtime.contexts, runtime.profiles
        )
        runtime.controllers[subentry_id] = controller
        await controller.async_setup()
        entry.async_on_unload(controller.async_shutdown)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(
    hass: HomeAssistant, entry: BetterLightingConfigEntry
) -> None:
    """Reload when the config actually changed.

    Verified against HA 2026.9.2: ``async_add_subentry`` / ``async_update_subentry``
    / ``async_remove_subentry`` all route through ``_async_update_entry``, which
    fires these listeners -- so subentry edits reach us here and no explicit
    reload call is needed in the subentry flows.
    """
    current = entry.runtime_data
    if current is not None and _fingerprint(entry) == current.config_fingerprint:
        _LOGGER.debug("Entry update changed nothing relevant; not reloading")
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: BetterLightingConfigEntry
) -> bool:
    """Tear down the hub entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
