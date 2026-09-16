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

from .const import PLATFORMS, BindingType, SubentryType
from .context import ContextRegistry
from .controllers import ControllerRuntime
from .models import (
    ControllerConfig,
    HubConfig,
    ModeConfig,
    ZoneConfig,
    synthetic_controller,
)
from .modes import ModeGroupRuntime
from .openings import WindowWatcher
from .presence import ZonePresence
from .profiles import LightProfile
from .repairs import async_check_references
from .scenes import Scene
from .services import async_register_services
from .session import DeferredRegistry
from .store import SessionStore
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
    # Scenes are zone-agnostic recipes, keyed by subentry_id so a rename
    # cannot break a reference.
    scenes: dict[str, Scene] = field(default_factory=dict)
    # One controller per zone; the only thing that commands member lights.
    controllers: dict[str, ZoneController] = field(default_factory=dict)
    # Configured switches, keyed by subentry_id.
    switches: dict[str, ControllerConfig] = field(default_factory=dict)
    switch_runtimes: dict[str, ControllerRuntime] = field(default_factory=dict)
    # The controller a bare turn-on on a zone's light entity is attributed to,
    # keyed by zone subentry_id.
    default_switch: dict[str, ControllerConfig] = field(default_factory=dict)
    # Cross-zone modes, and the deferred actions their sessions are waiting on.
    modes: dict[str, ModeConfig] = field(default_factory=dict)
    mode_runtimes: dict[str, ModeGroupRuntime] = field(default_factory=dict)
    deferred: DeferredRegistry = field(default_factory=DeferredRegistry)
    sessions: SessionStore | None = None
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
    # Calibration belongs to the room whose lights it calibrates, so the flat
    # registry is assembled from the zones -- as the scene registry is.
    profiles = {
        entity_id: profile
        for zone in zones.values()
        for entity_id, profile in zone.light_profiles.items()
    }
    # Scenes belong to rooms now, so the flat registry is assembled from the
    # zones rather than from subentries of its own. Keeping it flat means
    # repairs, diagnostics and the services still have one place to look.
    scenes = {scene.scene_id: scene for zone in zones.values() for scene in zone.scenes}
    for kind, label in (
        (SubentryType.SCENE.value, "scene"),
        (SubentryType.LIGHT_PROFILE.value, "light calibration"),
    ):
        legacy = [
            subentry
            for subentry in entry.subentries.values()
            if subentry.subentry_type == kind
        ]
        if legacy:
            _LOGGER.warning(
                "Ignoring %d %s(s) left over from before they belonged to rooms: "
                "%s. Re-create them under their room, then delete the old entries",
                len(legacy),
                label,
                ", ".join(sorted(sub.title for sub in legacy)),
            )
    switches = {
        controller.subentry_id: controller
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SubentryType.CONTROLLER.value
        and (controller := ControllerConfig.from_subentry(subentry))
    }

    # One controller per zone owns bare turn-ons. An explicitly flagged one
    # wins; otherwise any controller bound to the zone light; otherwise a
    # synthetic one, so a plain switch cycles without configuration.
    scene_ids = tuple(scenes)
    default_switch: dict[str, ControllerConfig] = {}
    for zone_id, zone in zones.items():
        candidates = [c for c in switches.values() if c.zone_id == zone_id]
        chosen = next((c for c in candidates if c.is_default), None)
        if chosen is None:
            chosen = next(
                (c for c in candidates if c.binding_type is BindingType.ZONE_LIGHT),
                None,
            )
        default_switch[zone_id] = chosen or synthetic_controller(zone, scene_ids)

    modes = {
        mode.subentry_id: mode
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SubentryType.MODE.value
        and (mode := ModeConfig.from_subentry(subentry))
    }

    return BetterLightingRuntime(
        hub=HubConfig.from_options(dict(entry.options)),
        zones=zones,
        profiles=profiles,
        scenes=scenes,
        switches=switches,
        default_switch=default_switch,
        modes=modes,
        config_fingerprint=_fingerprint(entry),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: BetterLightingConfigEntry
) -> bool:
    """Set up the hub entry."""
    runtime = build_runtime(entry)
    entry.runtime_data = runtime
    _LOGGER.debug(
        "Setting up with %d zone(s), %d scene(s), %d controller(s), "
        "%d light profile(s)",
        len(runtime.zones),
        len(runtime.scenes),
        len(runtime.switches),
        len(runtime.profiles),
    )

    for subentry_id, zone in runtime.zones.items():
        controller = ZoneController(
            hass,
            zone,
            runtime.hub,
            runtime.contexts,
            runtime.profiles,
            {scene.scene_id: scene for scene in zone.scenes},
        )
        runtime.controllers[subentry_id] = controller
        await controller.async_setup()
        entry.async_on_unload(controller.async_shutdown)

        presence = ZonePresence(
            hass,
            zone,
            on_occupied=_zone_occupied(hass, runtime, subentry_id),
            on_cleared=_zone_cleared(hass, runtime, subentry_id),
            on_gate_opened=_gate_opened(hass, controller),
        )
        controller.presence = presence
        await presence.async_setup()
        entry.async_on_unload(presence.async_shutdown)

        windows = WindowWatcher(
            hass,
            zone,
            on_open=_window_opened(hass, controller),
            on_closed=_window_closed(hass, controller),
        )
        controller.windows = windows
        await windows.async_setup()
        entry.async_on_unload(windows.async_shutdown)

    for controller in runtime.switches.values():
        zone_controller = runtime.controllers.get(controller.zone_id)
        if zone_controller is None:
            _LOGGER.warning(
                "Controller %r points at a zone that no longer exists; ignoring it",
                controller.name,
            )
            continue
        switch_runtime = ControllerRuntime(
            hass, controller, zone_controller, runtime.contexts
        )
        runtime.switch_runtimes[controller.subentry_id] = switch_runtime
        await switch_runtime.async_setup()
        entry.async_on_unload(switch_runtime.async_shutdown)

    runtime.sessions = SessionStore(hass)
    await runtime.sessions.async_load()

    for subentry_id, mode in runtime.modes.items():
        mode_runtime = ModeGroupRuntime(
            hass, mode, runtime.controllers, runtime.deferred, runtime.sessions
        )
        runtime.mode_runtimes[subentry_id] = mode_runtime
        await mode_runtime.async_setup()
        entry.async_on_unload(mode_runtime.async_shutdown)

    async_check_references(hass, entry.entry_id, runtime)

    async_register_services(hass)

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
    runtime = entry.runtime_data
    if runtime is not None and runtime.sessions is not None:
        # A reload is a restart in miniature. Flushing past the debounce here
        # is what stops an edit mid-film losing the session.
        await runtime.sessions.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _zone_occupied(hass: HomeAssistant, runtime: BetterLightingRuntime, zone_id: str):
    """Somebody has walked into this room.

    Modes are told first and the room's own presence rules second, because a
    mode driving the room owns its presence behaviour for the session -- the
    zone checks for that and stands down.
    """

    def _notify() -> None:
        hass.async_create_task(_async_occupied(runtime, zone_id))

    return _notify


async def _async_occupied(runtime: BetterLightingRuntime, zone_id: str) -> None:
    for mode_runtime in runtime.mode_runtimes.values():
        await mode_runtime.async_zone_occupied(zone_id)
    if (controller := runtime.controllers.get(zone_id)) is not None:
        await controller.async_presence_detected()


def _zone_cleared(hass: HomeAssistant, runtime: BetterLightingRuntime, zone_id: str):
    """This room has emptied."""

    def _notify() -> None:
        hass.async_create_task(_async_cleared(runtime, zone_id))

    return _notify


async def _async_cleared(runtime: BetterLightingRuntime, zone_id: str) -> None:
    for mode_runtime in runtime.mode_runtimes.values():
        await mode_runtime.async_zone_cleared(zone_id)
    if (controller := runtime.controllers.get(zone_id)) is not None:
        await controller.async_presence_cleared()


def _gate_opened(hass: HomeAssistant, controller: ZoneController):
    """The blinds came down while somebody was already in the room."""

    def _notify() -> None:
        hass.async_create_task(controller.async_cover_gate_opened())

    return _notify


def _window_opened(hass: HomeAssistant, controller: ZoneController):
    def _notify() -> None:
        hass.async_create_task(controller.async_window_opened())

    return _notify


def _window_closed(hass: HomeAssistant, controller: ZoneController):
    def _notify() -> None:
        hass.async_create_task(controller.async_window_closed())

    return _notify
