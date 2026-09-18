"""Config, options and subentry flows.

Every form is generated from the ``FieldSpec`` tables in :mod:`.const` via
:mod:`.schemas`, so adding an option means editing one table.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.selector import SelectOptionDict
from homeassistant.util import ulid as ulid_util

from .const import (
    COLOR_FORMAT_INHERIT,
    COLOR_FORMAT_NONE,
    COLOR_FORMAT_PRESET,
    COLOR_PRESET_SPECS,
    CONF_BRIGHTNESS_OFFSET_PCT,
    CONF_BRIGHTNESS_PCT,
    CONF_COLOR_FORMAT,
    CONF_COLOR_PRESETS,
    CONF_COLOR_TEMP_KELVIN,
    CONF_COLOR_TEMP_OFFSET_K,
    CONF_GROUP_GROUPS,
    CONF_GROUP_ID,
    CONF_GROUP_LIGHTS,
    CONF_GROUP_SEND_ENTITY,
    CONF_ICON,
    CONF_IGNORE_PRESENCE,
    CONF_LIGHT_ACTION,
    CONF_LIGHT_ENTITY,
    CONF_LIGHTS,
    CONF_MAX_BRIGHTNESS_PCT,
    CONF_MIN_BRIGHTNESS_PCT,
    CONF_NAME,
    CONF_ON_LIGHTS_ONLY,
    CONF_ON_UNSUPPORTED_COLOR,
    CONF_OTHERS,
    CONF_PRESENCE_ENTITY,
    CONF_PRESET_NAME,
    CONF_RESTORE_ON_POWER_CYCLE,
    CONF_RESUME_MAX_AGE_MIN,
    CONF_RGB_COLOR,
    CONF_ROOM_GROUPS,
    CONF_ROOM_ID,
    CONF_ROOM_PROFILES,
    CONF_ROOM_SCENES,
    CONF_ROOM_SWITCHES,
    CONF_ROOM_ZONES,
    CONF_RULE_ACTION,
    CONF_RULE_ENTRY_ACTION,
    CONF_RULE_ENTRY_SCENE,
    CONF_RULE_ROOMS,
    CONF_RULE_SCENE,
    CONF_RULE_STATES,
    CONF_RULES,
    CONF_SCENE_ID,
    CONF_SCENE_LIGHTS,
    CONF_SCENE_ORDER,
    CONF_STATES,
    CONF_SWITCH_ID,
    CONF_TRANSITION,
    CONF_WINDOW_ENTITIES,
    CONF_ZONE_DETACH_ON_MODE,
    CONF_ZONE_ID,
    CONF_ZONE_LIGHTS,
    CONTROLLER_SPECS,
    DOMAIN,
    HUB_SPECS,
    LIGHT_GROUP_SPECS,
    LIGHT_PROFILE_SPECS,
    MODE_SPECS,
    ROOM_SCENE_SPECS,
    ROOM_SPECS,
    ROOM_ZONE_SPECS,
    FieldSpec,
    RestoreOnPowerCycle,
    Section,
    SubentryType,
    mode_rule_specs,
    scene_light_color_specs,
    scene_light_specs,
)
from .groups import find_cycle
from .models import room_light_group, room_zone
from .scenes import ALL_LIGHTS
from .schemas import build_schema, flatten_sections, post_validate
from .zones import overlapping_lights

_LOGGER = logging.getLogger(__name__)

HUB_TITLE = "Better Lighting"


def scene_options(
    entry: ConfigEntry, room_id: str | None = None
) -> list[dict[str, str]]:
    """The scenes available to pick.

    When a room is given, only the scenes offered in that room -- so the list
    a switch cycles through cannot contain a scene meant for somewhere else.
    """
    options = []
    for sub in entry.subentries.values():
        if sub.subentry_type != SubentryType.ROOM.value:
            continue
        if room_id is not None and sub.subentry_id != room_id:
            continue
        for scene in sub.data.get(CONF_ROOM_SCENES) or ():
            if not scene.get(CONF_SCENE_ID):
                continue
            # Prefixed when the list spans rooms, because two rooms may well
            # each have a Reading scene and they are not the same thing.
            label = scene.get(CONF_NAME, "?")
            options.append(
                {
                    "value": scene[CONF_SCENE_ID],
                    "label": label if room_id is not None else f"{sub.title} · {label}",
                }
            )
    return options


def _room_subentries(entry: ConfigEntry) -> dict[str, Any]:
    """Every room subentry, keyed by subentry_id."""
    return {
        sub.subentry_id: sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SubentryType.ROOM.value
    }


def validate_room_lights(
    entry: ConfigEntry, lights: list[str], *, exclude_subentry_id: str | None = None
) -> dict[str, str]:
    """Enforce the one-light-one-room invariant.

    This is the rule the whole architecture rests on: because a light belongs to
    exactly one room, manual-override tracking, render ownership and press
    attribution are all unambiguous without any of Adaptive Lighting's
    multi-switch disambiguation machinery.
    """
    if not lights:
        return {CONF_LIGHTS: "no_lights"}

    claimed: dict[str, str] = {}
    for subentry_id, subentry in _room_subentries(entry).items():
        if subentry_id == exclude_subentry_id:
            continue
        for entity_id in subentry.data.get(CONF_LIGHTS) or ():
            claimed[entity_id] = subentry.title

    for entity_id in lights:
        if entity_id in claimed:
            _LOGGER.debug(
                "%s is already a member of room %r", entity_id, claimed[entity_id]
            )
            return {CONF_LIGHTS: "light_in_other_zone"}

    return {}


class BetterLightingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the single hub entry."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the hub, collecting the global adaptive defaults."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            flat = flatten_sections(HUB_SPECS, user_input)
            cleaned, errors = post_validate(HUB_SPECS, flat)
            if not errors:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=HUB_TITLE, data={}, options=cleaned
                )

        return self.async_show_form(
            step_id="user",
            data_schema=build_schema(
                HUB_SPECS, user_input, include=(Section.BASIC,), flat=True
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BetterLightingOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """The kinds of object that can be added to the hub."""
        return {
            SubentryType.ROOM.value: RoomSubentryFlow,
            SubentryType.MODE.value: ModeSubentryFlow,
        }


class BetterLightingOptionsFlow(OptionsFlow):
    """Edit the hub's global defaults and the house's colour presets."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._presets: list[dict[str, Any]] = []
        # An explicit flag, not "are the options empty": a hub that has never
        # been configured has empty options, and testing for that reloaded --
        # and so discarded -- every preset each time the menu was reopened.
        self._loaded = False
        self._pending: dict[str, Any] = {}
        self._editing: int | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._loaded:
            self._options = dict(self.config_entry.options)
            self._presets = [
                dict(preset) for preset in (self._options.get(CONF_COLOR_PRESETS) or [])
            ]
            self._loaded = True
        return self.async_show_menu(
            step_id="init",
            menu_options=["defaults", "night", "presets", "advanced", "finish"],
            description_placeholders={"presets": self._presets_summary()},
        )

    async def _async_section(
        self, step_id: str, section: Section, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """One group of the global settings, on a screen of its own."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = post_validate(HUB_SPECS, user_input)
            if not errors:
                self._options |= cleaned
                return await self.async_step_init()

        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema(
                HUB_SPECS,
                user_input or self._options,
                include=(section,),
                flat=True,
            ),
            errors=errors,
        )

    async def async_step_defaults(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_section("defaults", Section.BASIC, user_input)

    async def async_step_night(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_section("night", Section.NIGHT, user_input)

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_section("advanced", Section.ADVANCED, user_input)

    # -- colour presets ----------------------------------------------------

    def _presets_summary(self) -> str:
        if not self._presets:
            return "—"
        lines = []
        for preset in self._presets:
            if preset.get(CONF_COLOR_FORMAT) == CONF_COLOR_TEMP_KELVIN:
                detail = f"{preset.get(CONF_COLOR_TEMP_KELVIN)} K"
            else:
                rgb = preset.get(CONF_RGB_COLOR) or []
                detail = "RGB " + ",".join(str(v) for v in rgb)
            lines.append(f"{preset.get(CONF_NAME)}: {detail}")
        return "\n".join(lines)

    def _preset_picker(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("preset"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": str(index), "label": str(preset.get(CONF_NAME))}
                            for index, preset in enumerate(self._presets)
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )

    async def async_step_presets(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = ["add_preset"]
        if self._presets:
            options += ["edit_preset", "remove_preset"]
        options.append("init")
        return self.async_show_menu(
            step_id="presets",
            menu_options=options,
            description_placeholders={"presets": self._presets_summary()},
        )

    async def async_step_add_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_preset_form("add_preset", user_input, index=None)

    async def async_step_edit_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._editing = int(user_input["preset"])
            return await self.async_step_preset_form()
        return self.async_show_form(
            step_id="edit_preset",
            data_schema=self._preset_picker(),
            description_placeholders={"presets": self._presets_summary()},
        )

    async def async_step_preset_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_preset_form(
            "preset_form", user_input, index=self._editing
        )

    async def _async_preset_form(
        self, step_id: str, user_input: dict[str, Any] | None, *, index: int | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = post_validate(COLOR_PRESET_SPECS, user_input)
            if not errors:
                self._pending = cleaned
                self._editing = index
                return await self.async_step_preset_color()

        current = user_input
        if current is None and index is not None:
            current = dict(self._presets[index])
        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema(COLOR_PRESET_SPECS, current),
            errors=errors,
        )

    async def async_step_preset_color(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The colour itself, showing only the control the format asked for."""
        specs = scene_light_color_specs(self._pending.get(CONF_COLOR_FORMAT))
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = post_validate(specs, user_input)
            if not errors:
                preset = {**self._pending, **cleaned}
                if self._editing is not None and 0 <= self._editing < len(
                    self._presets
                ):
                    self._presets[self._editing] = preset
                else:
                    self._presets.append(preset)
                self._pending, self._editing = {}, None
                return await self.async_step_presets()

        return self.async_show_form(
            step_id="preset_color",
            data_schema=build_schema(specs, user_input),
            errors=errors,
            description_placeholders={"name": str(self._pending.get(CONF_NAME))},
        )

    async def async_step_remove_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            index = int(user_input["preset"])
            if 0 <= index < len(self._presets):
                self._presets.pop(index)
            return await self.async_step_presets()
        return self.async_show_form(
            step_id="remove_preset",
            data_schema=self._preset_picker(),
            description_placeholders={"presets": self._presets_summary()},
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(
            data={**self._options, CONF_COLOR_PRESETS: self._presets}
        )


def _light_group_cycle(items: list[dict[str, Any]]) -> list[str] | None:
    """The loop in a proposed set of light groups, by name, or None."""
    groups = {
        item[CONF_GROUP_ID]: room_light_group(item)
        for item in items
        if item.get(CONF_GROUP_ID)
    }
    if (cycle := find_cycle(groups)) is None:
        return None
    return [groups[group_id].name or group_id for group_id in cycle]


class RoomSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a room, and manage the scenes that belong to it.

    A room's settings come first, then a menu for its scenes. Scenes live here
    rather than in a list of their own because a scene is a list of *this*
    room's lights: a reading scene for the living room and one for the bedroom
    have nothing in common but the word, and putting them in one house-wide
    list only ever produced a picker nobody could read.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._scenes: list[dict[str, Any]] = []
        self._subentry: Any = None
        self._scene: dict[str, Any] = {}
        self._editing: int | None = None
        self._pending_light: tuple[str, dict[str, Any]] = ("", {})
        self._profiles: list[dict[str, Any]] = []
        self._editing_profile: int | None = None
        self._light_groups: list[dict[str, Any]] = []
        self._editing_light_group: int | None = None
        self._room_zones: list[dict[str, Any]] = []
        self._editing_room_zone: int | None = None
        self._switches: list[dict[str, Any]] = []
        self._switch: dict[str, Any] = {}
        self._editing_switch: int | None = None
        self._loaded = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Name the room and say which lights are in it. Everything else waits.

        Two fields to add a room, and the rest reached from a menu afterwards.
        A single long form with nine collapsed sections asks somebody adding
        their first room to scroll past presence, insect mode and power-cycle
        behaviour before they can press Submit.
        """
        return await self._async_essentials(user_input, subentry=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        self._load(subentry)
        return await self.async_step_menu()

    def _load(self, subentry: Any) -> None:
        """Take a copy of what is stored, to edit through the menu."""
        self._subentry = subentry
        if self._loaded:
            return
        self._loaded = True
        if subentry is None:
            return
        self._data = dict(subentry.data)
        self._scenes = [dict(s) for s in (subentry.data.get(CONF_ROOM_SCENES) or [])]
        self._switches = [
            dict(s) for s in (subentry.data.get(CONF_ROOM_SWITCHES) or [])
        ]
        self._profiles = [
            dict(s) for s in (subentry.data.get(CONF_ROOM_PROFILES) or [])
        ]
        self._light_groups = [
            dict(s) for s in (subentry.data.get(CONF_ROOM_GROUPS) or [])
        ]
        self._room_zones = [dict(s) for s in (subentry.data.get(CONF_ROOM_ZONES) or [])]

    async def _async_essentials(
        self, user_input: dict[str, Any] | None, *, subentry: Any
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        self._load(subentry)
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned, errors = post_validate(ROOM_SPECS, user_input)
            errors |= validate_room_lights(
                entry,
                cleaned.get(CONF_LIGHTS) or [],
                exclude_subentry_id=subentry.subentry_id if subentry else None,
            )
            if not errors:
                self._data |= cleaned
                return await self.async_step_menu()

        return self.async_show_form(
            step_id="user",
            data_schema=build_schema(
                ROOM_SPECS,
                user_input or self._data,
                include=(Section.BASIC,),
                flat=True,
            ),
            errors=errors,
        )

    # -- one screen per concern, all reached from the menu -----------------

    async def _async_section(
        self, step_id: str, section: Section, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """One group of the room's settings, on a screen of its own."""
        entry = self._get_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned, errors = post_validate(ROOM_SPECS, user_input)
            if section is Section.BASIC:
                errors |= validate_room_lights(
                    entry,
                    cleaned.get(CONF_LIGHTS) or [],
                    exclude_subentry_id=(
                        self._subentry.subentry_id if self._subentry else None
                    ),
                )
            if not errors:
                self._data |= cleaned
                return await self.async_step_menu()

        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema(
                ROOM_SPECS,
                user_input or self._data,
                include=(section,),
                flat=True,
                options={"scenes": self._scene_options()},
            ),
            errors=errors,
        )

    async def async_step_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_section("lights", Section.BASIC, user_input)

    async def async_step_group(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_section("group", Section.GROUP, user_input)

    async def async_step_adaptive(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_section("adaptive", Section.ADAPTIVE, user_input)

    async def async_step_night(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_section("night", Section.NIGHT, user_input)

    async def async_step_power(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """How the room comes back after the power was cut.

        The "forget the last scene after" limit only means anything when the
        room is set to resume that scene, so it is shown only then -- and
        choosing to resume re-renders this screen rather than making somebody
        come back to it to find the field that has just appeared.
        """
        errors: dict[str, str] = {}
        resumes = (
            self._data.get(CONF_RESTORE_ON_POWER_CYCLE)
            == RestoreOnPowerCycle.LAST_SCENE.value
        )
        if user_input is not None:
            cleaned, errors = post_validate(ROOM_SPECS, user_input)
            if not errors:
                self._data |= cleaned
                now_resumes = (
                    cleaned.get(CONF_RESTORE_ON_POWER_CYCLE)
                    == RestoreOnPowerCycle.LAST_SCENE.value
                )
                if now_resumes and not resumes:
                    return await self.async_step_power()
                return await self.async_step_menu()

        specs = tuple(
            spec
            for spec in ROOM_SPECS
            if spec.section is Section.POWER
            and (resumes or spec.key != CONF_RESUME_MAX_AGE_MIN)
        )
        return self.async_show_form(
            step_id="power",
            data_schema=build_schema(specs, user_input or self._data, flat=True),
            errors=errors,
        )

    async def async_step_presence(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_section("presence", Section.PRESENCE, user_input)

    async def async_step_insect(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_section("insect", Section.INSECT, user_input)

    async def async_step_summary(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Read the whole room back, without changing anything."""
        return self.async_show_menu(
            step_id="summary",
            menu_options=["menu"],
            description_placeholders={"summary": self._summary()},
        )

    def _summary(self) -> str:
        # Symbols rather than words: a description placeholder is substituted
        # verbatim and never translated, so anything written in English here
        # would stay English in every language.
        lights = self._data.get(CONF_LIGHTS) or ()
        lines = [
            f"💡 {len(lights)}",
            f"🎨 {len(self._scenes)}",
            f"🎚️ {len(self._switches)}",
            f"📐 {len(self._profiles)}",
        ]
        if presence := self._data.get(CONF_PRESENCE_ENTITY):
            lines.append(f"🚶 {presence}")
        if windows := self._data.get(CONF_WINDOW_ENTITIES):
            lines.append(f"🪟 {len(windows)}")
        return "\n".join(lines)

    # -- the room's own menu ----------------------------------------------

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="menu",
            menu_options=[
                # What the room is.
                "lights",
                "group",
                # What it does by itself.
                "adaptive",
                "night",
                "power",
                # What it does about people and windows.
                "presence",
                "insect",
                # What belongs to it.
                "scenes",
                "switches",
                "calibrations",
                "light_groups",
                "room_zones",
                # Read back, then leave.
                "summary",
                "finish",
            ],
            description_placeholders={"name": str(self._data.get(CONF_NAME, ""))},
        )

    async def async_step_scenes(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = ["add_scene", "capture_scene"]
        if self._scenes:
            options += ["edit_scene", "remove_scene"]
        if len(self._scenes) > 1:
            options.append("move_scene")
        options.append("menu")
        return self.async_show_menu(
            step_id="scenes",
            menu_options=options,
            description_placeholders={"scenes": self._scenes_summary()},
        )

    def _scenes_summary(self) -> str:
        if not self._scenes:
            return "—"
        lines = []
        for scene in self._scenes:
            lights = scene.get(CONF_SCENE_LIGHTS) or {}
            named = len([key for key in lights if key != ALL_LIGHTS])
            detail = []
            if ALL_LIGHTS in lights:
                detail.append("💡*")
            if named:
                detail.append(f"💡{named}")
            lines.append(
                f"{scene.get(CONF_NAME)}"
                + (f" — {', '.join(detail)}" if detail else "")
            )
        return "\n".join(lines)

    def _scene_options(self) -> list[SelectOptionDict]:
        """This room's scenes, for the night and insect pickers."""
        return [
            SelectOptionDict(
                value=str(scene.get(CONF_SCENE_ID)), label=scene[CONF_NAME]
            )
            for scene in self._scenes
            if scene.get(CONF_SCENE_ID)
        ]

    def _scene_picker(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("scene"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": str(index), "label": scene.get(CONF_NAME, "?")}
                            for index, scene in enumerate(self._scenes)
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )

    # -- adding and editing one scene -------------------------------------

    async def async_step_add_scene(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_scene_form("add_scene", user_input, index=None)

    async def async_step_edit_scene(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._editing = int(user_input["scene"])
            self._scene = dict(self._scenes[self._editing])
            return await self.async_step_scene_form()
        return self.async_show_form(
            step_id="edit_scene",
            data_schema=self._scene_picker(),
            description_placeholders={"scenes": self._scenes_summary()},
        )

    async def async_step_scene_form(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_scene_form(
            "scene_form", user_input, index=self._editing
        )

    async def _async_scene_form(
        self, step_id: str, user_input: dict[str, Any] | None, *, index: int | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = flatten_sections(ROOM_SCENE_SPECS, user_input)
            cleaned, errors = post_validate(ROOM_SCENE_SPECS, flat)
            if not errors:
                lights = self._scene.get(CONF_SCENE_LIGHTS) or {}
                self._scene = {
                    **cleaned,
                    CONF_SCENE_ID: self._scene.get(CONF_SCENE_ID)
                    or ulid_util.ulid_now(),
                    CONF_SCENE_LIGHTS: dict(lights),
                }
                self._editing = index
                return await self.async_step_scene_lights()

        current = user_input
        if current is None and index is not None:
            current = dict(self._scenes[index])
        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema(ROOM_SCENE_SPECS, current),
            errors=errors,
        )

    async def async_step_remove_scene(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            index = int(user_input["scene"])
            if 0 <= index < len(self._scenes):
                self._scenes.pop(index)
            return await self.async_step_scenes()
        return self.async_show_form(
            step_id="remove_scene",
            data_schema=self._scene_picker(),
            description_placeholders={"scenes": self._scenes_summary()},
        )

    async def async_step_move_scene(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reorder, because a switch cycles the room's scenes in this order."""
        if user_input is not None:
            index = int(user_input["scene"])
            target = max(0, min(len(self._scenes) - 1, int(user_input["position"]) - 1))
            scene = self._scenes.pop(index)
            self._scenes.insert(target, scene)
            return await self.async_step_scenes()

        schema = self._scene_picker().extend(
            {
                vol.Required("position", default=1): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=len(self._scenes),
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="move_scene",
            data_schema=schema,
            description_placeholders={"scenes": self._scenes_summary()},
        )

    # -- capturing what the room looks like now ---------------------------

    async def async_step_capture_scene(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Store the room exactly as it looks right now.

        The fastest way to build a scene, and the way round the fact that a
        config flow has no colour wheel: set the room up with Home Assistant's
        own light controls, then come here and name it.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            name = (user_input.get(CONF_NAME) or "").strip()
            if not name:
                errors[CONF_NAME] = "name_required"
            else:
                lights = self._capture_lights()
                if not lights:
                    errors["base"] = "nothing_to_capture"
                else:
                    self._scenes.append(
                        {
                            CONF_SCENE_ID: ulid_util.ulid_now(),
                            CONF_NAME: name,
                            CONF_ICON: "mdi:palette",
                            CONF_TRANSITION: 1.5,
                            CONF_ON_LIGHTS_ONLY: False,
                            CONF_IGNORE_PRESENCE: False,
                            CONF_OTHERS: "adaptive",
                            CONF_ON_UNSUPPORTED_COLOR: "adaptive",
                            CONF_SCENE_LIGHTS: lights,
                        }
                    )
                    return await self.async_step_scenes()

        return self.async_show_form(
            step_id="capture_scene",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): selector.TextSelector(
                        selector.TextSelectorConfig()
                    )
                }
            ),
            errors=errors,
            description_placeholders={"lights": self._capture_preview()},
        )

    def _capture_lights(self) -> dict[str, Any]:
        """Read the room's current state into per-light scene entries."""
        captured: dict[str, Any] = {}
        for entity_id in self._data.get(CONF_LIGHTS) or ():
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            if state.state != "on":
                captured[entity_id] = {CONF_LIGHT_ACTION: "off"}
                continue

            entry: dict[str, Any] = {CONF_LIGHT_ACTION: "apply"}
            if (brightness := state.attributes.get("brightness")) is not None:
                entry[CONF_BRIGHTNESS_PCT] = round(int(brightness) / 255 * 100, 1)

            # Whichever colour the light is actually showing. A light in
            # colour-temp mode has an rgb_color attribute too, derived rather
            # than set, and storing that would freeze a warm white into a
            # slightly-wrong orange.
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
            captured[entity_id] = entry
        return captured

    def _capture_preview(self) -> str:
        lines = []
        for entity_id in self._data.get(CONF_LIGHTS) or ():
            state = self.hass.states.get(entity_id)
            if state is None:
                lines.append(f"{entity_id}: unavailable")
            elif state.state != "on":
                lines.append(f"{entity_id}: off")
            else:
                brightness = state.attributes.get("brightness")
                pct = f"{round(int(brightness) / 255 * 100)}%" if brightness else "on"
                lines.append(f"{entity_id}: {pct}")
        return "\n".join(lines) or "(this room has no lights)"

    # -- the lights inside one scene --------------------------------------

    async def async_step_scene_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = ["add_light"]
        if self._scene.get(CONF_SCENE_LIGHTS):
            options += ["remove_light"]
        options.append("save_scene")
        return self.async_show_menu(
            step_id="scene_lights",
            menu_options=options,
            description_placeholders={"lights": self._lights_summary()},
        )

    def _lights_summary(self) -> str:
        lights = self._scene.get(CONF_SCENE_LIGHTS) or {}
        if not lights:
            return "—"
        lines = []
        for entity_id, entry in lights.items():
            label = "💡 *" if entity_id == ALL_LIGHTS else entity_id
            action = entry.get(CONF_LIGHT_ACTION, "apply")
            if action == "off":
                lines.append(f"{label}: ⭘")
                continue
            if action == "leave":
                lines.append(f"{label}: ↷")
                continue
            parts = []
            if (brightness := entry.get(CONF_BRIGHTNESS_PCT)) is not None:
                parts.append(f"{int(brightness)}%")
            fmt = entry.get(CONF_COLOR_FORMAT, COLOR_FORMAT_INHERIT)
            if fmt == COLOR_FORMAT_NONE:
                parts.append("☀")
            elif fmt == CONF_COLOR_TEMP_KELVIN and entry.get(CONF_COLOR_TEMP_KELVIN):
                parts.append(f"{entry[CONF_COLOR_TEMP_KELVIN]} K")
            elif fmt == CONF_RGB_COLOR and entry.get(CONF_RGB_COLOR):
                parts.append("RGB " + ",".join(str(v) for v in entry[CONF_RGB_COLOR]))
            elif fmt != COLOR_FORMAT_INHERIT:
                parts.append(str(fmt).replace("_", " "))
            lines.append(f"{label}: {', '.join(parts) or '—'}")
        return "\n".join(lines)

    async def async_step_add_light(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        specs = scene_light_specs()
        errors: dict[str, str] = {}
        lights = self._scene.setdefault(CONF_SCENE_LIGHTS, {})

        if user_input is not None:
            flat = flatten_sections(specs, user_input)
            cleaned, errors = post_validate(specs, flat)
            entity_id = cleaned.pop("light", None)
            if not entity_id:
                errors["light"] = "light_required"
            if not errors:
                self._pending_light = (entity_id, cleaned)
                if _light_needs_color(cleaned):
                    return await self.async_step_light_color()
                lights[entity_id] = cleaned
                return await self.async_step_scene_lights()

        available: list[SelectOptionDict] = [
            SelectOptionDict(value=ALL_LIGHTS, label="💡 *")
        ]
        available += [
            SelectOptionDict(value=entity_id, label=entity_id)
            for entity_id in (self._data.get(CONF_LIGHTS) or ())
        ]
        available = [option for option in available if option["value"] not in lights]
        if not available:
            return await self.async_step_scene_lights()

        return self.async_show_form(
            step_id="add_light",
            data_schema=build_schema(specs, user_input, options={"lights": available}),
            errors=errors,
            description_placeholders={"lights": self._lights_summary()},
        )

    def _presets(self) -> list[dict[str, Any]]:
        return list(self._get_entry().options.get(CONF_COLOR_PRESETS) or ())

    async def async_step_light_color(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entity_id, pending = self._pending_light
        fmt = pending.get(CONF_COLOR_FORMAT)
        errors: dict[str, str] = {}

        if fmt == COLOR_FORMAT_PRESET:
            presets = self._presets()
            if not presets:
                # Nothing to pick from. Better to say so than to show a dead
                # dropdown; the colour is left to the sun for now.
                self._scene.setdefault(CONF_SCENE_LIGHTS, {})[entity_id] = {
                    **pending,
                    CONF_COLOR_FORMAT: COLOR_FORMAT_NONE,
                }
                self._pending_light = ("", {})
                return await self.async_step_scene_lights()

            if user_input is not None:
                chosen = presets[int(user_input[CONF_PRESET_NAME])]
                # Copied, not referenced: editing a preset later must not
                # silently repaint scenes that were built with it.
                resolved = {
                    key: value
                    for key, value in chosen.items()
                    if key
                    in (CONF_COLOR_FORMAT, CONF_RGB_COLOR, CONF_COLOR_TEMP_KELVIN)
                }
                self._scene.setdefault(CONF_SCENE_LIGHTS, {})[entity_id] = {
                    **pending,
                    **resolved,
                }
                self._pending_light = ("", {})
                return await self.async_step_scene_lights()

            return self.async_show_form(
                step_id="light_preset",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_PRESET_NAME): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    {
                                        "value": str(index),
                                        "label": str(preset.get(CONF_NAME)),
                                    }
                                    for index, preset in enumerate(presets)
                                ],
                                mode=selector.SelectSelectorMode.DROPDOWN,
                                sort=False,
                            )
                        )
                    }
                ),
                description_placeholders={"light": entity_id},
            )

        specs = scene_light_color_specs(fmt)
        if user_input is not None:
            cleaned, errors = post_validate(specs, user_input)
            if not errors:
                self._scene.setdefault(CONF_SCENE_LIGHTS, {})[entity_id] = {
                    **pending,
                    **cleaned,
                }
                self._pending_light = ("", {})
                return await self.async_step_scene_lights()

        return self.async_show_form(
            step_id="light_color",
            data_schema=build_schema(specs, user_input),
            errors=errors,
            description_placeholders={"light": entity_id},
        )

    async def async_step_light_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self.async_step_light_color(user_input)

    async def async_step_remove_light(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        lights = self._scene.get(CONF_SCENE_LIGHTS) or {}
        if user_input is not None:
            lights.pop(user_input["light"], None)
            return await self.async_step_scene_lights()

        return self.async_show_form(
            step_id="remove_light",
            data_schema=vol.Schema(
                {
                    vol.Required("light"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": entity_id,
                                    "label": (
                                        "💡 *" if entity_id == ALL_LIGHTS else entity_id
                                    ),
                                }
                                for entity_id in lights
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            sort=False,
                        )
                    )
                }
            ),
            description_placeholders={"lights": self._lights_summary()},
        )

    async def async_step_save_scene(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        scene = dict(self._scene)
        if self._editing is not None and 0 <= self._editing < len(self._scenes):
            self._scenes[self._editing] = scene
        else:
            self._scenes.append(scene)
        self._scene = {}
        self._editing = None
        return await self.async_step_scenes()

    # -- calibrating individual lights ------------------------------------

    async def async_step_calibrations(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = ["add_calibration"]
        if self._profiles:
            options += ["edit_calibration", "remove_calibration"]
        options.append("menu")
        return self.async_show_menu(
            step_id="calibrations",
            menu_options=options,
            description_placeholders={"calibrations": self._calibrations_summary()},
        )

    def _calibrations_summary(self) -> str:
        if not self._profiles:
            return "—"
        lines = []
        for profile in self._profiles:
            parts = []
            if profile.get(CONF_BRIGHTNESS_OFFSET_PCT):
                parts.append(f"{profile[CONF_BRIGHTNESS_OFFSET_PCT]:+g}%")
            if profile.get(CONF_COLOR_TEMP_OFFSET_K):
                parts.append(f"{profile[CONF_COLOR_TEMP_OFFSET_K]:+g} K")
            parts.append(
                f"{profile.get(CONF_MIN_BRIGHTNESS_PCT, 1):g}"
                f" to {profile.get(CONF_MAX_BRIGHTNESS_PCT, 100):g}%"
            )
            lines.append(f"{profile.get(CONF_LIGHT_ENTITY)}: {', '.join(parts)}")
        return "\n".join(lines)

    def _calibration_picker(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("calibration"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {
                                "value": str(index),
                                "label": str(profile.get(CONF_LIGHT_ENTITY)),
                            }
                            for index, profile in enumerate(self._profiles)
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )

    async def async_step_add_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_calibration_form(
            "add_calibration", user_input, index=None
        )

    async def async_step_edit_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._editing_profile = int(user_input["calibration"])
            return await self.async_step_calibration_form()
        return self.async_show_form(
            step_id="edit_calibration",
            data_schema=self._calibration_picker(),
            description_placeholders={"calibrations": self._calibrations_summary()},
        )

    async def async_step_calibration_form(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_calibration_form(
            "calibration_form", user_input, index=self._editing_profile
        )

    async def _async_calibration_form(
        self, step_id: str, user_input: dict[str, Any] | None, *, index: int | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = flatten_sections(LIGHT_PROFILE_SPECS, user_input)
            cleaned, errors = post_validate(LIGHT_PROFILE_SPECS, flat)
            entity_id = cleaned.get(CONF_LIGHT_ENTITY)
            if not entity_id:
                errors[CONF_LIGHT_ENTITY] = "light_required"
            elif entity_id not in (self._data.get(CONF_LIGHTS) or ()):
                # A calibration belongs to the room that owns the light.
                errors[CONF_LIGHT_ENTITY] = "light_in_other_zone"
            if not errors:
                if index is None:
                    self._profiles.append(cleaned)
                else:
                    self._profiles[index] = cleaned
                self._editing_profile = None
                return await self.async_step_calibrations()

        current = user_input
        if current is None and index is not None:
            current = dict(self._profiles[index])
        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema(
                LIGHT_PROFILE_SPECS,
                current,
                options={
                    "lights": [
                        SelectOptionDict(value=entity_id, label=entity_id)
                        for entity_id in (self._data.get(CONF_LIGHTS) or ())
                    ]
                },
            ),
            errors=errors,
        )

    async def async_step_remove_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            index = int(user_input["calibration"])
            if 0 <= index < len(self._profiles):
                self._profiles.pop(index)
            return await self.async_step_calibrations()
        return self.async_show_form(
            step_id="remove_calibration",
            data_schema=self._calibration_picker(),
            description_placeholders={"calibrations": self._calibrations_summary()},
        )

    # -- parts of this room that can be told something different ------------

    async def async_step_room_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = ["add_room_zone"]
        if self._room_zones:
            options += ["edit_room_zone", "remove_room_zone"]
        options.append("menu")
        return self.async_show_menu(
            step_id="room_zones",
            menu_options=options,
            description_placeholders={"room_zones": self._room_zones_summary()},
        )

    def _room_zones_summary(self) -> str:
        if not self._room_zones:
            return "\u2014"
        lines = []
        for zone in self._room_zones:
            count = len(zone.get(CONF_ZONE_LIGHTS) or ())
            detaches = (
                "steps out while occupied"
                if zone.get(CONF_ZONE_DETACH_ON_MODE)
                else "always follows the room"
            )
            lines.append(f"{zone.get(CONF_NAME)}: {count} light(s), {detaches}")
        return "\n".join(lines)

    def _room_zone_picker(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("room_zone"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {
                                "value": str(index),
                                "label": str(zone.get(CONF_NAME) or index),
                            }
                            for index, zone in enumerate(self._room_zones)
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )

    async def async_step_add_room_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_room_zone_form("add_room_zone", user_input, index=None)

    async def async_step_edit_room_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._editing_room_zone = int(user_input["room_zone"])
            return await self.async_step_room_zone_form()
        return self.async_show_form(
            step_id="edit_room_zone",
            data_schema=self._room_zone_picker(),
            description_placeholders={"room_zones": self._room_zones_summary()},
        )

    async def async_step_room_zone_form(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_room_zone_form(
            "room_zone_form", user_input, index=self._editing_room_zone
        )

    async def _async_room_zone_form(
        self, step_id: str, user_input: dict[str, Any] | None, *, index: int | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = post_validate(
                ROOM_ZONE_SPECS, flatten_sections(ROOM_ZONE_SPECS, user_input)
            )
            if not cleaned.get(CONF_NAME):
                errors[CONF_NAME] = "name_required"
            stray = [
                light
                for light in (cleaned.get(CONF_ZONE_LIGHTS) or ())
                if light not in (self._data.get(CONF_LIGHTS) or ())
            ]
            if stray:
                # A zone is part of its room, so it can only hold the room's
                # own lights.
                errors[CONF_ZONE_LIGHTS] = "light_in_other_zone"
            if not errors:
                proposed = list(self._room_zones)
                merged = {
                    **(proposed[index] if index is not None else {}),
                    **cleaned,
                }
                merged.setdefault(CONF_ZONE_ID, ulid_util.ulid_now())
                if index is None:
                    proposed.append(merged)
                else:
                    proposed[index] = merged
                if overlapping_lights([room_zone(item) for item in proposed]):
                    errors[CONF_ZONE_LIGHTS] = "light_in_two_zones"
                else:
                    self._room_zones = proposed
                    self._editing_room_zone = None
                    return await self.async_step_room_zones()

        current = user_input
        if current is None and index is not None:
            current = dict(self._room_zones[index])
        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema(
                ROOM_ZONE_SPECS,
                current,
                options={
                    "lights": [
                        SelectOptionDict(value=entity_id, label=entity_id)
                        for entity_id in (self._data.get(CONF_LIGHTS) or ())
                    ],
                    "scenes": [
                        SelectOptionDict(
                            value=str(scene.get(CONF_SCENE_ID)),
                            label=str(scene.get(CONF_NAME)),
                        )
                        for scene in self._scenes
                        if scene.get(CONF_SCENE_ID)
                    ],
                },
            ),
            errors=errors,
        )

    async def async_step_remove_room_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            position = int(user_input["room_zone"])
            if 0 <= position < len(self._room_zones):
                self._room_zones.pop(position)
            return await self.async_step_room_zones()
        return self.async_show_form(
            step_id="remove_room_zone",
            data_schema=self._room_zone_picker(),
            description_placeholders={"room_zones": self._room_zones_summary()},
        )

    # -- named bundles of this room's lights -------------------------------

    async def async_step_light_groups(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = ["add_light_group"]
        if self._light_groups:
            options += ["edit_light_group", "remove_light_group"]
        options.append("menu")
        return self.async_show_menu(
            step_id="light_groups",
            menu_options=options,
            description_placeholders={"light_groups": self._light_groups_summary()},
        )

    def _light_groups_summary(self) -> str:
        if not self._light_groups:
            return "\u2014"
        lines = []
        for group in self._light_groups:
            held = len(group.get(CONF_GROUP_LIGHTS) or ()) + len(
                group.get(CONF_GROUP_GROUPS) or ()
            )
            sent = group.get(CONF_GROUP_SEND_ENTITY) or "individually"
            lines.append(f"{group.get(CONF_NAME)}: {held} member(s), via {sent}")
        return "\n".join(lines)

    def _light_group_picker(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("light_group"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {
                                "value": str(index),
                                "label": str(group.get(CONF_NAME) or index),
                            }
                            for index, group in enumerate(self._light_groups)
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )

    async def async_step_add_light_group(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_light_group_form(
            "add_light_group", user_input, index=None
        )

    async def async_step_edit_light_group(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._editing_light_group = int(user_input["light_group"])
            return await self.async_step_light_group_form()
        return self.async_show_form(
            step_id="edit_light_group",
            data_schema=self._light_group_picker(),
            description_placeholders={"light_groups": self._light_groups_summary()},
        )

    async def async_step_light_group_form(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_light_group_form(
            "light_group_form", user_input, index=self._editing_light_group
        )

    async def _async_light_group_form(
        self, step_id: str, user_input: dict[str, Any] | None, *, index: int | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = post_validate(
                LIGHT_GROUP_SPECS, flatten_sections(LIGHT_GROUP_SPECS, user_input)
            )
            if not cleaned.get(CONF_NAME):
                errors[CONF_NAME] = "name_required"
            if not errors:
                proposed = list(self._light_groups)
                merged = {
                    **(proposed[index] if index is not None else {}),
                    **cleaned,
                }
                merged.setdefault(CONF_GROUP_ID, ulid_util.ulid_now())
                if index is None:
                    proposed.append(merged)
                else:
                    proposed[index] = merged
                if _light_group_cycle(proposed):
                    # Refused rather than stored: a loop has no meaning to fall
                    # back to, and the room would lose every group it has.
                    errors[CONF_GROUP_GROUPS] = "group_cycle"
                else:
                    self._light_groups = proposed
                    self._editing_light_group = None
                    return await self.async_step_light_groups()

        current = user_input
        if current is None and index is not None:
            current = dict(self._light_groups[index])
        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema(
                LIGHT_GROUP_SPECS,
                current,
                options={
                    "light_groups": [
                        SelectOptionDict(
                            value=str(group.get(CONF_GROUP_ID)),
                            label=str(group.get(CONF_NAME) or group.get(CONF_GROUP_ID)),
                        )
                        # Not the one being edited: the one-step loop is best
                        # made unsayable rather than reported.
                        for position, group in enumerate(self._light_groups)
                        if position != index
                    ]
                },
            ),
            errors=errors,
        )

    async def async_step_remove_light_group(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            position = int(user_input["light_group"])
            if 0 <= position < len(self._light_groups):
                self._light_groups.pop(position)
            return await self.async_step_light_groups()
        return self.async_show_form(
            step_id="remove_light_group",
            data_schema=self._light_group_picker(),
            description_placeholders={"light_groups": self._light_groups_summary()},
        )

    # -- the switches on this room's walls --------------------------------

    async def async_step_switches(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = ["add_switch"]
        if self._switches:
            # "What it cycles" as its own entry: it used to be reachable only
            # by re-submitting the switch's settings, which is not somewhere
            # anybody thinks to look for a running order.
            options += ["order_switch", "edit_switch", "remove_switch"]
        options.append("menu")
        return self.async_show_menu(
            step_id="switches",
            menu_options=options,
            description_placeholders={"switches": self._switches_summary()},
        )

    def _switches_summary(self) -> str:
        if not self._switches:
            return "—"
        names = {
            str(scene.get(CONF_SCENE_ID)): str(scene.get(CONF_NAME))
            for scene in self._scenes
        }
        lines = []
        for switch in self._switches:
            order = [
                names.get(scene_id, scene_id)
                for scene_id in (switch.get(CONF_SCENE_ORDER) or ())
            ]
            lines.append(f"{switch.get(CONF_NAME)}: " + " → ".join(["☀", *order]))
        return "\n".join(lines)

    def _switch_picker(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("switch"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": str(index), "label": str(switch.get(CONF_NAME))}
                            for index, switch in enumerate(self._switches)
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )

    @staticmethod
    def _switch_specs() -> tuple[FieldSpec, ...]:
        """The controller form, minus the room it is in and its scene list.

        The room is implicit now that a switch lives inside one, and the list
        is built in a loop of its own rather than as a field.
        """
        return tuple(
            spec
            for spec in CONTROLLER_SPECS
            if spec.key not in (CONF_ROOM_ID, CONF_SCENE_ORDER)
        )

    async def async_step_add_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_switch_form("add_switch", user_input, index=None)

    async def async_step_edit_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._editing_switch = int(user_input["switch"])
            self._switch = dict(self._switches[self._editing_switch])
            return await self.async_step_switch_form()
        return self.async_show_form(
            step_id="edit_switch",
            data_schema=self._switch_picker(),
            description_placeholders={"switches": self._switches_summary()},
        )

    async def async_step_switch_form(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_switch_form(
            "switch_form", user_input, index=self._editing_switch
        )

    async def _async_switch_form(
        self, step_id: str, user_input: dict[str, Any] | None, *, index: int | None
    ) -> SubentryFlowResult:
        specs = self._switch_specs()
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = flatten_sections(specs, user_input)
            cleaned, errors = post_validate(specs, flat)
            if not errors:
                self._switch = {
                    **cleaned,
                    CONF_SWITCH_ID: self._switch.get(CONF_SWITCH_ID)
                    or ulid_util.ulid_now(),
                    CONF_SCENE_ORDER: list(self._switch.get(CONF_SCENE_ORDER) or ()),
                }
                self._editing_switch = index
                return await self.async_step_order()

        current = user_input
        if current is None and index is not None:
            current = dict(self._switches[index])
        return self.async_show_form(
            step_id=step_id, data_schema=build_schema(specs, current), errors=errors
        )

    async def async_step_remove_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            index = int(user_input["switch"])
            if 0 <= index < len(self._switches):
                self._switches.pop(index)
            return await self.async_step_switches()
        return self.async_show_form(
            step_id="remove_switch",
            data_schema=self._switch_picker(),
            description_placeholders={"switches": self._switches_summary()},
        )

    async def async_step_order_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick a switch, then edit the list it cycles."""
        if user_input is not None:
            self._editing_switch = int(user_input["switch"])
            self._switch = dict(self._switches[self._editing_switch])
            return await self.async_step_order()
        return self.async_show_form(
            step_id="order_switch",
            data_schema=self._switch_picker(),
            description_placeholders={"switches": self._switches_summary()},
        )

    # -- what this switch cycles through ----------------------------------

    @property
    def _order(self) -> list[str]:
        return self._switch.setdefault(CONF_SCENE_ORDER, [])

    def _order_summary(self) -> str:
        names = {
            str(scene.get(CONF_SCENE_ID)): str(scene.get(CONF_NAME))
            for scene in self._scenes
        }
        lines = ["1. ☀"]
        for index, scene_id in enumerate(self._order, start=2):
            lines.append(f"{index}. {names.get(scene_id, scene_id)}")
        return "\n".join(lines)

    def _unused_scenes(self) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(
                value=str(scene[CONF_SCENE_ID]), label=str(scene.get(CONF_NAME))
            )
            for scene in self._scenes
            if scene.get(CONF_SCENE_ID) and scene[CONF_SCENE_ID] not in self._order
        ]

    def _ordered_selector(self) -> Any:
        names = {
            str(scene.get(CONF_SCENE_ID)): str(scene.get(CONF_NAME))
            for scene in self._scenes
        }
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {
                        "value": scene_id,
                        "label": f"{index}. {names.get(scene_id, scene_id)}",
                    }
                    for index, scene_id in enumerate(self._order, start=2)
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
                sort=False,
            )
        )

    async def async_step_order(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = []
        if self._unused_scenes():
            options.append("add_step")
        if self._order:
            options += ["move_step", "remove_step"]
        options.append("save_switch")
        return self.async_show_menu(
            step_id="order",
            menu_options=options,
            description_placeholders={"order": self._order_summary()},
        )

    async def async_step_add_step(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        available = self._unused_scenes()
        if not available:
            return await self.async_step_order()
        if user_input is not None:
            self._order.append(user_input["scene"])
            return await self.async_step_order()
        return self.async_show_form(
            step_id="add_step",
            data_schema=vol.Schema(
                {
                    vol.Required("scene"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=available,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            sort=False,
                        )
                    )
                }
            ),
            description_placeholders={"order": self._order_summary()},
        )

    async def async_step_remove_step(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            if user_input["scene"] in self._order:
                self._order.remove(user_input["scene"])
            return await self.async_step_order()
        return self.async_show_form(
            step_id="remove_step",
            data_schema=vol.Schema({vol.Required("scene"): self._ordered_selector()}),
            description_placeholders={"order": self._order_summary()},
        )

    async def async_step_move_step(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            scene_id = user_input["scene"]
            # Positions are shown counting Adaptive as 1, so the list index is
            # two behind what the user typed.
            position = int(user_input["position"]) - 2
            if scene_id in self._order:
                self._order.remove(scene_id)
                self._order.insert(max(0, min(position, len(self._order))), scene_id)
            return await self.async_step_order()

        return self.async_show_form(
            step_id="move_step",
            data_schema=vol.Schema(
                {
                    vol.Required("scene"): self._ordered_selector(),
                    vol.Required("position", default=2): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=2,
                            max=max(len(self._order) + 1, 2),
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            description_placeholders={"order": self._order_summary()},
        )

    async def async_step_save_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        switch = dict(self._switch)
        if self._editing_switch is not None and 0 <= self._editing_switch < len(
            self._switches
        ):
            self._switches[self._editing_switch] = switch
        else:
            self._switches.append(switch)
        self._switch = {}
        self._editing_switch = None
        return await self.async_step_switches()

    # -- done --------------------------------------------------------------

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        data = {
            **self._data,
            CONF_ROOM_SCENES: self._scenes,
            CONF_ROOM_PROFILES: self._profiles,
            CONF_ROOM_GROUPS: self._light_groups,
            CONF_ROOM_ZONES: self._room_zones,
            CONF_ROOM_SWITCHES: self._switches,
        }
        title = data[CONF_NAME]
        if self._subentry is None:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(
            self._get_entry(), self._subentry, data=data, title=title
        )


def room_options(entry: ConfigEntry) -> list[dict[str, str]]:
    """The rooms defined so far, for a controller to be bound to."""
    return [
        {"value": sub.subentry_id, "label": sub.title}
        for sub in entry.subentries.values()
        if sub.subentry_type == SubentryType.ROOM.value
    ]


class ModeSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a cross-room mode, such as Home Cinema.

    Settings first, then a loop for the rules. A rule can name several states
    and several rooms at once, so "these three rooms go dark while the film is
    playing or the credits roll" is one form rather than nine.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._rules: list[dict[str, Any]] = []
        self._subentry: Any = None
        # Index of the rule being changed, while the edit form is open.
        self._editing: int | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_settings(user_input, subentry=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_settings(
            user_input, subentry=self._get_reconfigure_subentry()
        )

    async def _async_settings(
        self, user_input: dict[str, Any] | None, *, subentry: Any
    ) -> SubentryFlowResult:
        """Name the mode and its states, then open its menu."""
        self._subentry = subentry
        if subentry is not None and not self._data:
            self._data = dict(subentry.data)
            self._rules = [dict(r) for r in (subentry.data.get(CONF_RULES) or [])]
            return await self.async_step_menu()

        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = post_validate(MODE_SPECS, user_input)
            if not cleaned.get(CONF_STATES):
                errors[CONF_STATES] = "no_states"
            if not errors:
                self._data |= cleaned
                known = set(cleaned[CONF_STATES])
                # Drop rules naming states that no longer exist.
                self._rules = [
                    rule
                    for rule in self._rules
                    if set(rule.get(CONF_RULE_STATES) or ()) & known
                ]
                return await self.async_step_menu()

        return self.async_show_form(
            step_id="user",
            data_schema=build_schema(
                MODE_SPECS,
                user_input or self._data,
                include=(Section.BASIC,),
                flat=True,
            ),
            errors=errors,
        )

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="menu",
            menu_options=["settings", "rules", "behaviour", "finish"],
            description_placeholders={"rules": self._rules_summary()},
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_settings(user_input, subentry=self._subentry)

    async def async_step_behaviour(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Snapshots and what happens when the mode ends."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = post_validate(MODE_SPECS, user_input)
            if not errors:
                self._data |= cleaned
                return await self.async_step_menu()

        return self.async_show_form(
            step_id="behaviour",
            data_schema=build_schema(
                MODE_SPECS,
                user_input or self._data,
                include=(Section.ADVANCED,),
                flat=True,
            ),
            errors=errors,
        )

    # -- the rules loop ----------------------------------------------------

    def _rules_summary(self) -> str:
        entry = self._get_entry()
        rooms = {z["value"]: z["label"] for z in room_options(entry)}
        scenes = {s["value"]: s["label"] for s in scene_options(entry)}
        if not self._rules:
            return "(no rules yet -- the mode will not change anything)"
        lines = []
        for index, rule in enumerate(self._rules, start=1):
            states = ", ".join(rule.get(CONF_RULE_STATES) or ())
            rooms = ", ".join(
                rooms.get(z, z) for z in (rule.get(CONF_RULE_ROOMS) or ())
            )
            action = rule.get(CONF_RULE_ACTION, "keep")
            if action == "apply_scene":
                action = f"apply {scenes.get(rule.get(CONF_RULE_SCENE), '?')}"
            lines.append(f"{index}. [{states}] {rooms}: {action}")
        return "\n".join(lines)

    async def async_step_rules(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = ["add_rule"]
        if self._rules:
            options += ["edit_rule", "remove_rule"]
        options.append("menu")
        return self.async_show_menu(
            step_id="rules",
            menu_options=options,
            description_placeholders={"rules": self._rules_summary()},
        )

    async def async_step_add_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_rule_form("add_rule", user_input, index=None)

    async def async_step_edit_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick a rule to change. The form then opens filled in.

        Editing rather than delete-and-recreate: a rule carries eight fields,
        and retyping all of them to move one room between two states is how
        mistakes get made.
        """
        if user_input is not None:
            self._editing = int(user_input["rule"])
            return await self.async_step_rule_form()

        return self.async_show_form(
            step_id="edit_rule",
            data_schema=self._rule_picker(),
            description_placeholders={"rules": self._rules_summary()},
        )

    async def async_step_rule_form(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_rule_form("rule_form", user_input, index=self._editing)

    async def _async_rule_form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        *,
        index: int | None,
    ) -> SubentryFlowResult:
        """One rule form, used both to add a rule and to change one."""
        entry = self._get_entry()
        specs = mode_rule_specs(list(self._data.get(CONF_STATES) or ()))
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = flatten_sections(specs, user_input)
            cleaned, errors = post_validate(specs, flat)
            if cleaned.get(CONF_RULE_ACTION) == "apply_scene" and not cleaned.get(
                CONF_RULE_SCENE
            ):
                errors[CONF_RULE_SCENE] = "scene_required"
            if cleaned.get(CONF_RULE_ENTRY_ACTION) == "apply_scene" and not cleaned.get(
                CONF_RULE_ENTRY_SCENE
            ):
                errors[CONF_RULE_ENTRY_SCENE] = "scene_required"
            # A scene belongs to one room, so a rule cannot borrow another
            # room's. Checked here rather than by narrowing the picker,
            # because the room is chosen on the same form as the scene.
            room = cleaned.get(CONF_RULE_ROOMS)
            if room:
                theirs = {s["value"] for s in scene_options(entry, room)}
                for key in (CONF_RULE_SCENE, CONF_RULE_ENTRY_SCENE):
                    if cleaned.get(key) and cleaned[key] not in theirs:
                        errors[key] = "scene_in_other_zone"
            if not errors:
                if index is None:
                    self._rules.append(cleaned)
                elif 0 <= index < len(self._rules):
                    self._rules[index] = cleaned
                self._editing = None
                return await self.async_step_rules()

        current = user_input
        if current is None and index is not None and 0 <= index < len(self._rules):
            current = dict(self._rules[index])

        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema(
                specs,
                current,
                options={
                    "zones": room_options(entry),
                    "scenes": scene_options(entry),
                },
            ),
            errors=errors,
            description_placeholders={"rules": self._rules_summary()},
        )

    def _rule_picker(self) -> vol.Schema:
        """A dropdown of the rules as they read in the summary."""
        return vol.Schema(
            {
                vol.Required("rule"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": str(index), "label": line}
                            for index, line in enumerate(
                                self._rules_summary().split("\n")
                            )
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )

    async def async_step_remove_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            index = int(user_input["rule"])
            if 0 <= index < len(self._rules):
                self._rules.pop(index)
            return await self.async_step_rules()

        return self.async_show_form(
            step_id="remove_rule",
            data_schema=self._rule_picker(),
            description_placeholders={"rules": self._rules_summary()},
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        data = {**self._data, CONF_RULES: self._rules}
        title = data[CONF_NAME]
        if self._subentry is None:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(
            self._get_entry(), self._subentry, data=data, title=title
        )


def _light_needs_color(entry: dict[str, Any]) -> bool:
    """Whether this light's colour needs a value of its own."""
    return entry.get(CONF_COLOR_FORMAT) not in (
        None,
        COLOR_FORMAT_INHERIT,
        COLOR_FORMAT_NONE,
    )
