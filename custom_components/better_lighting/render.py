"""The render pipeline: one function decides what every light in a zone does.

All four scene override modes (requirement 7) fall out of a single mask
operation rather than four code paths:

    engine_axes = ALL & ~manual[light]
    scene_axes  = mode_mask & what_the_scene_actually_specifies & engine_axes
    adapt_axes  = engine_axes & ~scene_axes

and the subtle half of the requirement -- "keep adaptive brightness but
overwrite colour" means the interval must *keep updating* the axis the scene did
not claim -- is the whole of :func:`emitted_axes`.

Two invariants hold throughout, and are tested:

* a TICK never turns a light on and never turns one off. It only adjusts what
  is already lit, so the periodic refresh can never surprise anyone.
* night and insect are not render paths. They resolve to a scene (or to a set
  of curve parameters) *before* rendering, so there is one pipeline, not five.

Pure: imports nothing from ``homeassistant`` except colour conversions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from .adaptive import AdaptiveSettings
from .profiles import (
    IDENTITY_PROFILE,
    Axis,
    LightCapabilities,
    LightProfile,
    Saturation,
    brightness_from_pct,
    resolve_target,
)
from .scenes import (
    COLOR_ATTRS,
    SCENE_AXES,
    OthersPolicy,
    Scene,
    select_color_attrs,
)

ATTR_BRIGHTNESS = "brightness"
ATTR_TRANSITION = "transition"


class Trigger(StrEnum):
    """Why a render is happening. Decides which axes may be emitted."""

    # A mode or scene change, a zone turning on, a manual reset: make one
    # visible move, carrying every axis the engine owns.
    ACTIVATE = "activate"
    # The periodic refresh. Only the axes the scene did not claim.
    TICK = "tick"
    # A member went off -> on and needs catching up.
    TURN_ON = "turn_on"
    # A relative dim: brightness only.
    DIM = "dim"
    # An explicit service call or diagnostic.
    FORCE = "force"


class ZoneMode(StrEnum):
    """What a zone is currently doing."""

    OFF = "off"
    ADAPTIVE = "adaptive"
    SCENE = "scene"
    NIGHT = "night"
    INSECT = "insect"
    EXTERNAL = "external"


def emitted_axes(trigger: Trigger, scene_axes: Axis, adapt_axes: Axis) -> Axis:
    """Which axes this particular render is allowed to send.

    The TICK branch is the entire partial-adaptive mechanism. Re-sending a
    scene's own axis every interval would be redundant traffic, and would stomp
    anything layered on top of it -- a relative dim, or a nudge the user made
    that has not yet crossed the manual-override threshold.
    """
    if trigger is Trigger.TICK:
        return adapt_axes
    if trigger is Trigger.DIM:
        return Axis.BRIGHTNESS & (scene_axes | adapt_axes)
    return scene_axes | adapt_axes


@dataclass(frozen=True, slots=True)
class LightSnapshot:
    """A member light as it is right now."""

    entity_id: str
    is_on: bool
    available: bool
    caps: LightCapabilities
    brightness: int | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LightCommand:
    """One instruction for one light."""

    entity_id: str
    action: Literal["turn_on", "turn_off"]
    data: dict[str, Any]
    axes: Axis = Axis.NONE
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RenderResult:
    commands: list[LightCommand]
    saturation: dict[str, Saturation] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RenderRequest:
    """Everything needed to decide what a zone's lights should do."""

    mode: ZoneMode
    trigger: Trigger
    settings: AdaptiveSettings
    members: Sequence[LightSnapshot]
    scene: Scene | None = None
    profiles: Mapping[str, LightProfile] = field(default_factory=dict)
    # Axes the engine has given up on, per light, because a human moved them.
    # Per (zone, light) by construction: this map belongs to one zone.
    manual: Mapping[str, Axis] = field(default_factory=dict)
    bias_pct: float = 0.0
    transition: float | None = None
    # When set, only these members may be switched on -- used to restore the
    # set that was lit before a cross-zone mode took over.
    restore_members: frozenset[str] | None = None


def render_zone(request: RenderRequest) -> RenderResult:
    """Decide what every light in a zone should be doing."""
    commands: list[LightCommand] = []
    saturation: dict[str, Saturation] = {}
    skipped: dict[str, str] = {}

    scene = request.scene
    members = [member for member in request.members if member.available]
    on_now = {member.entity_id for member in members if member.is_on}

    # -- which lights this render is about --------------------------------
    if scene is not None and scene.lights:
        targets = {m.entity_id for m in members if scene.targets(m.entity_id)}
    else:
        targets = {m.entity_id for m in members}
    if request.restore_members is not None:
        targets &= request.restore_members
    if scene is not None and scene.on_lights_only:
        # Requirement 6: a scene may adjust the room without lighting it up.
        targets &= on_now
    if request.trigger is Trigger.TICK:
        targets &= on_now

    if request.mode is ZoneMode.OFF:
        data = {ATTR_TRANSITION: request.transition} if request.transition else {}
        return RenderResult(
            [
                LightCommand(entity_id, "turn_off", dict(data), reason="zone_off")
                for entity_id in sorted(on_now)
            ]
        )

    for member in members:
        entity_id = member.entity_id
        profile = request.profiles.get(entity_id, IDENTITY_PROFILE)

        # -- axis ownership: the one place the four scene modes are decided --
        engine_axes = Axis.ALL & ~request.manual.get(entity_id, Axis.NONE)
        if request.mode is ZoneMode.EXTERNAL:
            # Someone else is driving this zone; observe, emit nothing.
            engine_axes = Axis.NONE

        in_scene = scene is not None and entity_id in targets
        scene_axes = (
            SCENE_AXES[scene.override]
            & scene.axes_specified_for(entity_id)
            & engine_axes
            if in_scene
            else Axis.NONE
        )
        adapt_axes = engine_axes & ~scene_axes
        emit = emitted_axes(request.trigger, scene_axes, adapt_axes)

        # -- members the scene does not name ------------------------------
        if entity_id not in targets:
            policy = scene.others if scene is not None else OthersPolicy.ADAPTIVE
            if policy is OthersPolicy.ADAPTIVE:
                if entity_id not in on_now:
                    continue  # dark stays dark
                emit &= adapt_axes
                scene_axes = Axis.NONE
            elif policy is OthersPolicy.OFF:
                if entity_id in on_now and request.trigger is not Trigger.TICK:
                    data = (
                        {ATTR_TRANSITION: request.transition}
                        if request.transition
                        else {}
                    )
                    commands.append(
                        LightCommand(
                            entity_id, "turn_off", data, reason="scene_others_off"
                        )
                    )
                continue
            else:  # LEAVE
                skipped[entity_id] = "others_leave"
                continue

        if emit is Axis.NONE:
            skipped[entity_id] = "manual" if engine_axes is Axis.NONE else "no_axes"
            continue

        # -- build one payload from two sources ---------------------------
        data: dict[str, Any] = {}

        adaptive = resolve_target(
            request.settings,
            profile,
            member.caps,
            want=emit & adapt_axes,
            bias_pct=request.bias_pct,
        )
        if adaptive.saturation:
            saturation[entity_id] = adaptive.saturation
        if adaptive.brightness is not None:
            data[ATTR_BRIGHTNESS] = adaptive.brightness
        if adaptive.color:
            data |= adaptive.color

        if scene is not None and Axis.BRIGHTNESS in (emit & scene_axes):
            spec = scene.spec_for(entity_id)
            brightness, scene_saturation = brightness_from_pct(
                spec.brightness_pct,
                profile,
                member.caps,
                bias_pct=request.bias_pct,
                respect_adapt_flag=False,
            )
            if scene_saturation:
                saturation[entity_id] = saturation.get(entity_id, Saturation.NONE) | (
                    scene_saturation
                )
            if brightness is not None:
                data[ATTR_BRIGHTNESS] = brightness

        if scene is not None and Axis.COLOR in (emit & scene_axes):
            spec = scene.spec_for(entity_id)
            color = select_color_attrs(
                spec.color,
                member.caps,
                adaptive_kelvin=request.settings.color_temp_kelvin,
                policy=scene.on_unsupported_color,
            )
            if color:
                # The scene owns this axis outright, so drop anything the
                # adaptive pass put there rather than merging two colours.
                for attr in COLOR_ATTRS:
                    data.pop(attr, None)
                data |= color

        if not any(key in data for key in (ATTR_BRIGHTNESS, *COLOR_ATTRS)):
            skipped[entity_id] = "empty_payload"
            continue

        transition = (
            scene.transition
            if scene is not None and scene.transition is not None
            else request.transition
        )
        if transition and member.caps.supports_transition:
            data[ATTR_TRANSITION] = transition

        commands.append(
            LightCommand(
                entity_id,
                "turn_on",
                data,
                axes=emit,
                reason=f"{request.mode}/{request.trigger}",
            )
        )

    return RenderResult(commands, saturation, skipped)


def batch(commands: Sequence[LightCommand]) -> list[tuple[str, dict[str, Any]]]:
    """Merge identical payloads so one service call covers several lights."""
    groups: dict[tuple, list[str]] = {}
    payloads: dict[tuple, dict[str, Any]] = {}
    for command in commands:
        key = (
            command.action,
            tuple(
                sorted(
                    (name, tuple(value) if isinstance(value, list | tuple) else value)
                    for name, value in command.data.items()
                )
            ),
        )
        groups.setdefault(key, []).append(command.entity_id)
        payloads.setdefault(key, command.data)
    return [
        (key[0], {**payloads[key], "entity_id": sorted(entity_ids)})
        for key, entity_ids in groups.items()
    ]
