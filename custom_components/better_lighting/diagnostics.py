"""What the integration currently believes, for a bug report.

Deliberately includes the *derived* state as well as the configuration --
which axes each room thinks a human has taken over, which calibrations are
being clipped, what each mode session is waiting on -- because those are what
questions about surprising behaviour actually turn on.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import BetterLightingConfigEntry
from .cycle import StepKind
from .profiles import describe_saturation


def _step_name(step: Any, runtime: Any) -> str:
    """One position in a cycle, in words.

    The ids were unreadable, which made the one question anybody actually
    asks of this list -- "why does my switch do that at the end?" --
    impossible to answer by looking at it.
    """
    if step.kind is StepKind.SCENE:
        scene = runtime.scenes.get(step.scene_id)
        return scene.name if scene is not None else f"{step.scene_id} (missing)"
    return step.kind.value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BetterLightingConfigEntry
) -> dict[str, Any]:
    """Dump the hub's configuration and live state."""
    runtime = entry.runtime_data

    return {
        "hub": _as_dict(runtime.hub),
        "rooms": {
            room_id: _room_diagnostics(runtime, room_id) for room_id in runtime.rooms
        },
        "scenes": {
            scene_id: {
                "name": scene.name,
                "override": scene.override.value,
                "brightness_pct": scene.brightness_pct,
                "color": dict(scene.color) if scene.color else None,
                "on_lights_only": scene.on_lights_only,
                "ignore_presence": scene.ignore_presence,
                "others": scene.others.value,
            }
            for scene_id, scene in runtime.scenes.items()
        },
        "controllers": {
            controller_id: {
                "name": controller.name,
                "zone_id": controller.room_id,
                "binding": controller.binding_type.value,
                "binding_entity": controller.binding_entity,
                "is_default": controller.is_default,
                "cycle": [
                    _step_name(step, runtime) for step in controller.cycle().steps
                ],
                # What the switch has actually published lately, and what each
                # value was read as. A value the vocabulary has no word for
                # produces no press and so leaves no other trace, which is
                # what makes a half-configured switch so hard to diagnose.
                "seen": list(
                    getattr(runtime.switch_runtimes.get(controller_id), "seen", ())
                ),
            }
            for controller_id, controller in runtime.switches.items()
        },
        "light_profiles": {
            entity_id: _as_dict(profile)
            for entity_id, profile in runtime.profiles.items()
        },
        "modes": {
            mode_id: _mode_diagnostics(runtime, mode_id) for mode_id in runtime.modes
        },
        "deferred": [
            {
                "zone_id": action.room_id,
                "session_id": action.session_id,
                "mode_state": action.mode_state,
                "action": action.action.value,
            }
            for action in runtime.deferred
        ],
        "contexts_tracked": len(runtime.contexts),
    }


def _room_diagnostics(runtime: Any, room_id: str) -> dict[str, Any]:
    room = runtime.rooms[room_id]
    controller = runtime.controllers.get(room_id)
    data: dict[str, Any] = {
        "name": room.name,
        "lights": list(room.lights),
        "adaptive_override": room.adaptive_override,
        "night_source": room.night_source_entity,
        "night_behavior": room.night_behavior.value,
        "triggers": [t.entity_id for t in room.triggers],
        "presence_covers": list(room.presence_covers),
        "window_entities": list(room.window_entities),
        "restore_on_power_cycle": room.restore_on_power_cycle.value,
    }
    if controller is None:
        return data

    data |= {
        # "Why did my light not go off?" is the question this page exists for,
        # and these three hold every answer to it: somebody is holding it on,
        # a rule is blocking the automation, or a zone is standing apart.
        "held_by_hand": controller.held_by_hand,
        "zones_held_by_hand": sorted(controller.zones_held_by_hand),
        "rules": {
            (rule.name or rule.kind.value): bool(controller.rules_allow([rule]))
            for rule in room.rules
        },
        "detached_zones": sorted(controller.detached_zones),
        "mode": controller.mode.value,
        "effective_mode": controller.effective_mode.value,
        "active_scene_id": controller.active_scene_id,
        "adaptive_enabled": controller.adaptive_enabled,
        "night_active": controller.night_active,
        "insect_active": controller.insect_active,
        "insect_dismissed": controller.insect_dismissed,
        "bias_pct": controller.bias_pct,
        "session_owner": controller.session_owner,
        # The two most common sources of "why is this light not adapting?".
        "manual": {
            entity_id: str(axes) for entity_id, axes in controller.manual.items()
        },
        "saturated": {
            entity_id: describe_saturation(flags)
            for entity_id, flags in controller.saturated_lights.items()
        },
    }
    if controller.presence is not None:
        data["presence"] = {
            "occupied": controller.presence.occupied,
            "covers_ok": controller.presence.covers_ok,
        }
    if controller.windows is not None:
        data["window_open"] = controller.windows.is_open
    return data


def _mode_diagnostics(runtime: Any, mode_id: str) -> dict[str, Any]:
    mode = runtime.modes[mode_id]
    mode_runtime = runtime.mode_runtimes.get(mode_id)
    data: dict[str, Any] = {
        "name": mode.name,
        "states": list(mode.states),
        "rooms": sorted(mode.room_ids),
        "rules": [
            {
                "states": sorted(rule.states),
                "rooms": sorted(rule.rooms),
                "action": rule.action.value,
                "scene_id": rule.scene_id,
                "respect_presence": rule.respect_presence,
                "defer_if_occupied": rule.defer_if_occupied,
                "scripts": list(rule.scripts),
            }
            for rule in mode.rules
        ],
    }
    if mode_runtime is not None:
        data |= {
            "state": mode_runtime.state,
            "enabled": mode_runtime.enabled,
            "session_id": mode_runtime.session_id,
            "opted_out": sorted(mode_runtime.opted_out),
            "snapshot_taken_at": (
                mode_runtime.snapshot.taken_at if mode_runtime.snapshot else None
            ),
            "snapshot_previously_on": (
                {
                    room_id: sorted(room_snapshot.previously_on)
                    for room_id, room_snapshot in mode_runtime.snapshot.rooms.items()
                }
                if mode_runtime.snapshot
                else None
            ),
        }
    return data


def _as_dict(obj: Any) -> dict[str, Any]:
    """A dataclass as plain JSON-safe values.

    Tuples become lists because this dump is read two ways -- downloaded, and
    over the websocket to the panel -- and only one of those goes through
    JSON. Leaving them as tuples made the two disagree about a field nobody
    had looked at, which is exactly the sort of difference that makes a
    diagnostics page worth less than the bug report it is meant to support.
    """
    from dataclasses import asdict, is_dataclass

    if not is_dataclass(obj):
        return {}
    return {key: _plain(value) for key, value in asdict(obj).items()}


def _plain(value: Any) -> Any:
    if hasattr(value, "value"):  # an enum
        return value.value
    if isinstance(value, tuple | set | frozenset):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value
