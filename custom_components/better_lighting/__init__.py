"""The Better Lighting integration.

A single hub config entry owns every configuration object as a subentry, so
scenes are reusable across rooms and a cross-room mode has one place to live.
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .activity import ActivityLog
from .const import (
    CONF_ROOM_SCENES,
    CONF_ROOM_SWITCHES,
    CONF_SCENE_ID,
    CONF_SCENE_ORDER,
    CONF_SCENE_ORDER_EXCLUDED,
    DOMAIN,
    PLATFORMS,
    BindingType,
    SubentryType,
)
from .context import ContextRegistry
from .controllers import EVENT_PRESS, ControllerRuntime
from .cycle import ADAPTIVE_STEP
from .models import (
    ControllerConfig,
    HubConfig,
    ModeConfig,
    RoomConfig,
    synthetic_controller,
)
from .modes import (
    EVENT_DEFERRED,
    EVENT_MODE_CHANGED,
    EVENT_ROOM_OPTED_OUT,
    ModeGroupRuntime,
)
from .openings import WindowWatcher
from .panel import (
    async_register_commands,
    async_remove_panel,
    async_setup_panel,
)
from .presence import RoomPresence
from .profiles import LightProfile
from .repairs import async_check_legacy, async_check_references
from .room import EVENT_ROOM_MODE_CHANGED, RoomController
from .scenes import Scene
from .services import async_register_services, async_remove_services
from .session import DeferredRegistry
from .simulate import SimulationRunner
from .store import SessionStore

# Everything worth remembering, which is the same set the panel watches.
ACTIVITY_EVENTS = (
    EVENT_PRESS,
    EVENT_ROOM_MODE_CHANGED,
    EVENT_MODE_CHANGED,
    EVENT_DEFERRED,
    EVENT_ROOM_OPTED_OUT,
)

if TYPE_CHECKING:
    from .light import RoomLight

_LOGGER = logging.getLogger(__name__)

type BetterLightingConfigEntry = ConfigEntry[BetterLightingRuntime]


@dataclass(slots=True)
class BetterLightingRuntime:
    """Everything the integration knows, for the lifetime of one entry load."""

    hub: HubConfig
    rooms: dict[str, RoomConfig]
    # Per-light calibration, keyed by light entity_id. A light belongs to one
    # room, so one profile per light is unambiguous.
    profiles: dict[str, LightProfile] = field(default_factory=dict)
    # Scenes are room-agnostic recipes, keyed by subentry_id so a rename
    # cannot break a reference.
    scenes: dict[str, Scene] = field(default_factory=dict)
    # One controller per room; the only thing that commands member lights.
    controllers: dict[str, RoomController] = field(default_factory=dict)
    # Configured switches, keyed by subentry_id.
    switches: dict[str, ControllerConfig] = field(default_factory=dict)
    switch_runtimes: dict[str, ControllerRuntime] = field(default_factory=dict)
    activity: ActivityLog | None = None
    # The controller a bare turn-on on a room's light entity is attributed to,
    # keyed by room subentry_id.
    default_switch: dict[str, ControllerConfig] = field(default_factory=dict)
    # Cross-room modes, and the deferred actions their sessions are waiting on.
    modes: dict[str, ModeConfig] = field(default_factory=dict)
    mode_runtimes: dict[str, ModeGroupRuntime] = field(default_factory=dict)
    deferred: DeferredRegistry = field(default_factory=DeferredRegistry)
    sessions: SessionStore | None = None
    # Live entity objects, registered as their platforms come up. Keyed by
    # room subentry_id so any subsystem can reach a room without a global.
    room_lights: dict[str, RoomLight] = field(default_factory=dict)
    contexts: ContextRegistry = field(default_factory=ContextRegistry)
    # Makes an empty house look lived in. One per hub, because being away is
    # a fact about the house rather than about any room in it.
    simulation: SimulationRunner | None = None
    # Fingerprint of the config this runtime was built from, so an update
    # callback that changes nothing does not trigger a reload storm.
    config_fingerprint: int = 0


def _fingerprint(entry: ConfigEntry) -> int:
    """A cheap hash of everything a reload would rebuild.

    Config values contain lists (a room's lights), so this serialises to sorted
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
    rooms = {
        subentry.subentry_id: RoomConfig.from_subentry(subentry)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SubentryType.ROOM.value
    }
    # Calibration belongs to the room whose lights it calibrates, so the flat
    # registry is assembled from the rooms -- as the scene registry is.
    profiles = {
        entity_id: profile
        for room in rooms.values()
        for entity_id, profile in room.light_profiles.items()
    }
    # Scenes belong to rooms now, so the flat registry is assembled from the
    # rooms rather than from subentries of its own. Keeping it flat means
    # repairs, diagnostics and the services still have one place to look.
    scenes = {scene.scene_id: scene for room in rooms.values() for scene in room.scenes}
    for kind, label in (
        (SubentryType.SCENE.value, "scene"),
        (SubentryType.LIGHT_PROFILE.value, "light calibration"),
        (SubentryType.CONTROLLER.value, "light switch"),
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
    # Switches belong to the room they drive, as scenes and calibration do.
    switches = {
        switch.subentry_id: switch
        for room in rooms.values()
        for switch in room.switches
    }

    # One controller per room owns bare turn-ons. An explicitly flagged one
    # wins; otherwise any controller bound to the room light; otherwise a
    # synthetic one, so a plain switch cycles without configuration.
    scene_ids = tuple(scenes)
    default_switch: dict[str, ControllerConfig] = {}
    for room_id, room in rooms.items():
        candidates = [c for c in switches.values() if c.room_id == room_id]
        chosen = next((c for c in candidates if c.is_default), None)
        if chosen is None:
            chosen = next(
                (c for c in candidates if c.binding_type is BindingType.ROOM_LIGHT),
                None,
            )
        default_switch[room_id] = chosen or synthetic_controller(room, scene_ids)

    modes = {
        mode.subentry_id: mode
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SubentryType.MODE.value
        and (mode := ModeConfig.from_subentry(subentry))
    }

    return BetterLightingRuntime(
        hub=HubConfig.from_options(dict(entry.options)),
        rooms=rooms,
        profiles=profiles,
        scenes=scenes,
        switches=switches,
        default_switch=default_switch,
        modes=modes,
        config_fingerprint=_fingerprint(entry),
    )


@callback
def _async_tidy_switch_orders(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Take scenes that no longer exist out of what a switch stores.

    A deleted scene leaves its id behind in every switch that cycled it. The
    runtime has always skipped those, so the room behaved -- but they sit in
    the configuration for ever, and they are what a repair notice counts when
    it says a switch references something missing.

    Rewritten in place rather than migrated once and recorded: it is the same
    work either way, it is correct for a scene deleted tomorrow as well as one
    deleted last year, and writing only when something actually changed means
    it settles after a single pass.
    """
    for subentry in list(entry.subentries.values()):
        if subentry.subentry_type != SubentryType.ROOM.value:
            continue
        scenes = {
            scene.get(CONF_SCENE_ID)
            for scene in (subentry.data.get(CONF_ROOM_SCENES) or ())
        }
        known = {*scenes, ADAPTIVE_STEP}
        switches = [
            dict(item) for item in (subentry.data.get(CONF_ROOM_SWITCHES) or ())
        ]
        changed = False
        for switch in switches:
            for key in (CONF_SCENE_ORDER, CONF_SCENE_ORDER_EXCLUDED):
                stored = list(switch.get(key) or ())
                kept = [scene_id for scene_id in stored if scene_id in known]
                if kept != stored:
                    switch[key] = kept
                    changed = True
        if changed:
            _LOGGER.debug(
                "Tidied scenes that no longer exist out of %s's switches",
                subentry.title,
            )
            hass.config_entries.async_update_subentry(
                entry, subentry, data={**subentry.data, CONF_ROOM_SWITCHES: switches}
            )


@callback
def _async_prune_entities(
    hass: HomeAssistant, entry: ConfigEntry, runtime: BetterLightingRuntime
) -> None:
    """Remove entities whose object is gone.

    Every entity we create is named after the thing it belongs to, so an
    entity whose id no longer matches anything configured has outlived it.
    Switches are the case that matters: they live inside a room now, so
    deleting one does not delete a subentry and nothing else would ever
    clear its press entity.
    """
    # The house-wide button is named after the entry rather than after any
    # room, mode or switch -- without this it is swept away and recreated on
    # every reload, losing whatever the user renamed or hid.
    known = {*runtime.rooms, *runtime.modes, *runtime.switches, entry.entry_id}
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = entity.unique_id or ""
        if not any(unique_id.startswith(f"{owner}_") for owner in known):
            _LOGGER.debug("Removing %s, whose owner is gone", entity.entity_id)
            registry.async_remove(entity.entity_id)


@callback
def _async_prune_devices(
    hass: HomeAssistant, entry: ConfigEntry, runtime: BetterLightingRuntime
) -> None:
    """Remove devices for objects that no longer exist.

    Switches used to own a device each. They are part of their room now, and a
    switch deleted under the old arrangement left its device and a dead entity
    behind -- so anything of ours not matching a room or a mode is swept up.
    """
    # The hub's own device carries the house-wide button, and belongs to no
    # room or mode -- so it has to be named here or the sweep takes it.
    known = {*runtime.rooms, *runtime.modes, entry.entry_id}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        ours = {
            identifier for domain, identifier in device.identifiers if domain == DOMAIN
        }
        if ours and not ours & known:
            _LOGGER.debug("Removing device left behind by %s", ", ".join(sorted(ours)))
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def async_setup_entry(
    hass: HomeAssistant, entry: BetterLightingConfigEntry
) -> bool:
    """Set up the hub entry."""
    await async_setup_panel(hass)
    async_register_commands(hass)
    runtime = build_runtime(entry)
    entry.runtime_data = runtime
    _LOGGER.debug(
        "Setting up with %d room(s), %d scene(s), %d controller(s), "
        "%d light profile(s)",
        len(runtime.rooms),
        len(runtime.scenes),
        len(runtime.switches),
        len(runtime.profiles),
    )

    for subentry_id, room in runtime.rooms.items():
        controller = RoomController(
            hass,
            room,
            runtime.hub,
            runtime.contexts,
            runtime.profiles,
            {scene.scene_id: scene for scene in room.scenes},
        )
        runtime.controllers[subentry_id] = controller
        await controller.async_setup()
        entry.async_on_unload(controller.async_shutdown)

        presence = RoomPresence(
            hass,
            room,
            on_occupied=_room_occupied(hass, runtime, subentry_id),
            on_cleared=_room_cleared(hass, runtime, subentry_id),
            on_gate_opened=_gate_opened(hass, controller),
        )
        controller.presence = presence
        await presence.async_setup()
        entry.async_on_unload(presence.async_shutdown)

        windows = WindowWatcher(
            hass,
            room,
            on_open=_window_opened(hass, controller),
            on_closed=_window_closed(hass, controller),
        )
        controller.windows = windows
        await windows.async_setup()
        entry.async_on_unload(windows.async_shutdown)

    for controller in runtime.switches.values():
        room_controller = runtime.controllers.get(controller.room_id)
        if room_controller is None:
            _LOGGER.warning(
                "Controller %r points at a room that no longer exists; ignoring it",
                controller.name,
            )
            continue
        switch_runtime = ControllerRuntime(
            hass, controller, room_controller, runtime.contexts
        )
        runtime.switch_runtimes[controller.subentry_id] = switch_runtime
        await switch_runtime.async_setup()
        entry.async_on_unload(switch_runtime.async_shutdown)

    runtime.sessions = SessionStore(hass)
    await runtime.sessions.async_load()

    # The same events the panel watches live, kept for as long as the global
    # settings say -- so a switch that misbehaved in the night can still be
    # looked into in the morning.
    runtime.activity = ActivityLog(hass, runtime.hub.log_retention_hours)
    await runtime.activity.async_load()
    for kind in ACTIVITY_EVENTS:
        entry.async_on_unload(
            hass.bus.async_listen(kind, runtime.activity.async_record)
        )

    for subentry_id, mode in runtime.modes.items():
        mode_runtime = ModeGroupRuntime(
            hass, mode, runtime.controllers, runtime.deferred, runtime.sessions
        )
        runtime.mode_runtimes[subentry_id] = mode_runtime
        await mode_runtime.async_setup()
        entry.async_on_unload(mode_runtime.async_shutdown)

    # After the rooms, because it drives them; and after the modes, because
    # a mode running while the house is empty is a stranger situation than
    # anything this does and should win by having got there first.
    runtime.simulation = SimulationRunner(hass, runtime)
    await runtime.simulation.async_setup()
    entry.async_on_unload(runtime.simulation.async_shutdown)

    _async_tidy_switch_orders(hass, entry)
    _async_prune_entities(hass, entry, runtime)
    _async_prune_devices(hass, entry, runtime)

    if runtime.sessions is not None and (
        dropped := runtime.sessions.prune(set(runtime.modes))
    ):
        _LOGGER.debug("Dropped %d stored session(s) for modes that are gone", dropped)

    async_check_references(hass, entry.entry_id, runtime)
    async_check_legacy(hass, entry)

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
    async_remove_panel(hass)
    runtime = entry.runtime_data
    if runtime is not None and runtime.sessions is not None:
        # A reload is a restart in miniature. Flushing past the debounce here
        # is what stops an edit mid-film losing the session.
        await runtime.sessions.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _room_occupied(hass: HomeAssistant, runtime: BetterLightingRuntime, room_id: str):
    """Somebody has walked into this room.

    Modes are told first and the room's own presence rules second, because a
    mode driving the room owns its presence behaviour for the session -- the
    room checks for that and stands down.
    """

    def _notify() -> None:
        hass.async_create_task(_async_occupied(runtime, room_id))

    return _notify


async def _async_occupied(runtime: BetterLightingRuntime, room_id: str) -> None:
    for mode_runtime in runtime.mode_runtimes.values():
        await mode_runtime.async_room_occupied(room_id)
    if (controller := runtime.controllers.get(room_id)) is not None:
        await controller.async_presence_detected()


def _room_cleared(hass: HomeAssistant, runtime: BetterLightingRuntime, room_id: str):
    """This room has emptied."""

    def _notify() -> None:
        hass.async_create_task(_async_cleared(runtime, room_id))

    return _notify


async def _async_cleared(runtime: BetterLightingRuntime, room_id: str) -> None:
    for mode_runtime in runtime.mode_runtimes.values():
        await mode_runtime.async_room_cleared(room_id)
    if (controller := runtime.controllers.get(room_id)) is not None:
        await controller.async_presence_cleared()


def _gate_opened(hass: HomeAssistant, controller: RoomController):
    """The blinds came down while somebody was already in the room."""

    def _notify() -> None:
        hass.async_create_task(controller.async_cover_gate_opened())

    return _notify


def _window_opened(hass: HomeAssistant, controller: RoomController):
    def _notify() -> None:
        hass.async_create_task(controller.async_window_opened())

    return _notify


def _window_closed(hass: HomeAssistant, controller: RoomController):
    def _notify() -> None:
        hass.async_create_task(controller.async_window_closed())

    return _notify


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up what outlives the config entry.

    Unloading tidies memory; this runs when the integration is being removed
    for good, and has to take the things Home Assistant does not know are
    ours: the session file, and any repair issues we raised.
    """
    async_remove_services(hass)

    store = SessionStore(hass)
    await store.async_remove()
    await ActivityLog(hass, 0).async_remove()

    for issue in list(ir.async_get(hass).issues.values()):
        if issue.domain == DOMAIN:
            ir.async_delete_issue(hass, DOMAIN, issue.issue_id)
