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

from .const import (
    COLOR_FORMAT_NONE,
    CONF_ADAPTIVE_POSITION,
    CONF_BINDING_ENTITY,
    CONF_BINDING_TYPE,
    CONF_COLOR_FORMAT,
    CONF_LIGHT_ENTITY,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OFF_AT_END,
    CONF_OVERRIDE_MODE,
    CONF_SCENE_ORDER,
    CONTROLLER_SPECS,
    DOMAIN,
    HUB_SPECS,
    LIGHT_PROFILE_SPECS,
    SCENE_COLOR_SPECS,
    SCENE_SPECS,
    ZONE_SPECS,
    BindingType,
    SubentryType,
)
from .schemas import build_schema, flatten_sections, post_validate

_LOGGER = logging.getLogger(__name__)

HUB_TITLE = "Better Lighting"


def scene_options(entry: ConfigEntry) -> list[dict[str, str]]:
    """The scenes defined so far, for any form that needs to pick one."""
    return [
        {"value": sub.subentry_id, "label": sub.title}
        for sub in entry.subentries.values()
        if sub.subentry_type == SubentryType.SCENE.value
    ]


def _zone_subentries(entry: ConfigEntry) -> dict[str, Any]:
    """Every zone subentry, keyed by subentry_id."""
    return {
        sub.subentry_id: sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SubentryType.ZONE.value
    }


def validate_zone_lights(
    entry: ConfigEntry, lights: list[str], *, exclude_subentry_id: str | None = None
) -> dict[str, str]:
    """Enforce the one-light-one-zone invariant.

    This is the rule the whole architecture rests on: because a light belongs to
    exactly one zone, manual-override tracking, render ownership and press
    attribution are all unambiguous without any of Adaptive Lighting's
    multi-switch disambiguation machinery.
    """
    if not lights:
        return {CONF_LIGHTS: "no_lights"}

    claimed: dict[str, str] = {}
    for subentry_id, subentry in _zone_subentries(entry).items():
        if subentry_id == exclude_subentry_id:
            continue
        for entity_id in subentry.data.get(CONF_LIGHTS) or ():
            claimed[entity_id] = subentry.title

    for entity_id in lights:
        if entity_id in claimed:
            _LOGGER.debug(
                "%s is already a member of zone %r", entity_id, claimed[entity_id]
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
            data_schema=build_schema(HUB_SPECS, user_input),
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
            SubentryType.ZONE.value: ZoneSubentryFlow,
            SubentryType.LIGHT_PROFILE.value: LightProfileSubentryFlow,
            SubentryType.SCENE.value: SceneSubentryFlow,
            SubentryType.CONTROLLER.value: ControllerSubentryFlow,
        }


class BetterLightingOptionsFlow(OptionsFlow):
    """Edit the hub's global defaults."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = flatten_sections(HUB_SPECS, user_input)
            cleaned, errors = post_validate(HUB_SPECS, flat)
            if not errors:
                return self.async_create_entry(data=cleaned)

        return self.async_show_form(
            step_id="init",
            data_schema=build_schema(
                HUB_SPECS, user_input or dict(self.config_entry.options)
            ),
            errors=errors,
        )


class ZoneSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a zone."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_zone_form(user_input, subentry=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_zone_form(
            user_input, subentry=self._get_reconfigure_subentry()
        )

    async def _async_zone_form(
        self, user_input: dict[str, Any] | None, *, subentry: Any
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = flatten_sections(ZONE_SPECS, user_input)
            cleaned, errors = post_validate(ZONE_SPECS, flat)
            errors |= validate_zone_lights(
                entry,
                cleaned.get(CONF_LIGHTS) or [],
                exclude_subentry_id=subentry.subentry_id if subentry else None,
            )
            if not errors:
                title = cleaned[CONF_NAME]
                if subentry is None:
                    return self.async_create_entry(title=title, data=cleaned)
                return self.async_update_and_abort(
                    entry, subentry, data=cleaned, title=title
                )

        existing = dict(subentry.data) if subentry else None
        scenes = scene_options(entry)
        return self.async_show_form(
            step_id="reconfigure" if subentry else "user",
            data_schema=build_schema(
                ZONE_SPECS, user_input or existing, options={"scenes": scenes}
            ),
            errors=errors,
            description_placeholders={
                "scene_hint": (
                    ""
                    if scenes
                    else "No scenes defined yet, so the night scene cannot be "
                    "chosen. Add one, then reconfigure this zone."
                )
            },
        )


def validate_light_profile(
    entry: ConfigEntry, light_entity: str, *, exclude_subentry_id: str | None = None
) -> dict[str, str]:
    """One calibration per light.

    A light belongs to one zone, so a second profile for it could only ever be
    a contradiction. HA's subentry unique_id would reject it anyway, but doing
    it here produces an error on the right field instead of an opaque abort.
    """
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SubentryType.LIGHT_PROFILE.value:
            continue
        if subentry.subentry_id == exclude_subentry_id:
            continue
        if subentry.data.get(CONF_LIGHT_ENTITY) == light_entity:
            return {CONF_LIGHT_ENTITY: "profile_exists"}
    return {}


class LightProfileSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a per-light calibration."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_profile_form(user_input, subentry=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_profile_form(
            user_input, subentry=self._get_reconfigure_subentry()
        )

    async def _async_profile_form(
        self, user_input: dict[str, Any] | None, *, subentry: Any
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = flatten_sections(LIGHT_PROFILE_SPECS, user_input)
            cleaned, errors = post_validate(LIGHT_PROFILE_SPECS, flat)
            light_entity = cleaned.get(CONF_LIGHT_ENTITY)
            if light_entity:
                errors |= validate_light_profile(
                    entry,
                    light_entity,
                    exclude_subentry_id=subentry.subentry_id if subentry else None,
                )
            if not errors:
                title = _profile_title(self.hass, light_entity)
                if subentry is None:
                    return self.async_create_entry(
                        title=title,
                        data=cleaned,
                        unique_id=f"profile:{light_entity}",
                    )
                return self.async_update_and_abort(
                    entry, subentry, data=cleaned, title=title
                )

        existing = dict(subentry.data) if subentry else None
        return self.async_show_form(
            step_id="reconfigure" if subentry else "user",
            data_schema=build_schema(LIGHT_PROFILE_SPECS, user_input or existing),
            errors=errors,
        )


def _profile_title(hass, light_entity: str | None) -> str:
    """Name the subentry after the light, so the list is readable."""
    if not light_entity:
        return "Light profile"
    state = hass.states.get(light_entity)
    if state is not None and (name := state.attributes.get("friendly_name")):
        return str(name)
    return light_entity.removeprefix("light.").replace("_", " ").title()


class SceneSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a scene.

    Two steps on purpose. A colour must carry exactly one format, and the only
    reliable way to guarantee that through a form is to ask which format first
    and then show that one field -- rather than offer eight and hope the user
    fills in a single one.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._subentry: Any = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_basics(user_input, subentry=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_basics(
            user_input, subentry=self._get_reconfigure_subentry()
        )

    async def _async_basics(
        self, user_input: dict[str, Any] | None, *, subentry: Any
    ) -> SubentryFlowResult:
        self._subentry = subentry
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = flatten_sections(SCENE_SPECS, user_input)
            cleaned, errors = post_validate(SCENE_SPECS, flat)
            if not errors:
                self._data = cleaned
                if self._needs_color(cleaned):
                    return await self.async_step_color()
                return self._finish()

        existing = dict(subentry.data) if subentry else None
        return self.async_show_form(
            step_id="reconfigure" if subentry else "user",
            data_schema=build_schema(SCENE_SPECS, user_input or existing),
            errors=errors,
        )

    async def async_step_color(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect the one colour field the chosen format needs."""
        fmt = self._data.get(CONF_COLOR_FORMAT)
        spec = SCENE_COLOR_SPECS.get(fmt)
        if spec is None:
            return self._finish()

        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = post_validate((spec,), user_input)
            if not errors:
                # Only the chosen format survives, so a format switched during
                # a reconfigure cannot leave a second colour behind.
                for other in SCENE_COLOR_SPECS:
                    self._data.pop(other, None)
                self._data |= cleaned
                return self._finish()

        existing = dict(self._subentry.data) if self._subentry else None
        return self.async_show_form(
            step_id="color",
            data_schema=build_schema((spec,), user_input or existing),
            errors=errors,
            description_placeholders={"format": str(fmt)},
        )

    @staticmethod
    def _needs_color(data: dict[str, Any]) -> bool:
        if data.get(CONF_OVERRIDE_MODE) in ("brightness", "neither"):
            return False
        return data.get(CONF_COLOR_FORMAT) not in (None, COLOR_FORMAT_NONE)

    def _finish(self) -> SubentryFlowResult:
        title = self._data[CONF_NAME]
        if self._subentry is None:
            return self.async_create_entry(title=title, data=self._data)
        return self.async_update_and_abort(
            self._get_entry(), self._subentry, data=self._data, title=title
        )


def zone_options(entry: ConfigEntry) -> list[dict[str, str]]:
    """The zones defined so far, for a controller to be bound to."""
    return [
        {"value": sub.subentry_id, "label": sub.title}
        for sub in entry.subentries.values()
        if sub.subentry_type == SubentryType.ZONE.value
    ]


class ControllerSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a light switch and its ordered list of scenes.

    The ordering is a menu loop rather than a single multi-select, because
    Home Assistant's SelectSelector has no reorder support -- only the entity,
    area and floor selectors do -- and the order of a multi-select's returned
    value is not something to rely on. A loop is more clicks but the resulting
    order is exactly what the user built.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._order: list[str] = []
        self._subentry: Any = None

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
        self._subentry = subentry
        entry = self._get_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = flatten_sections(CONTROLLER_SPECS, user_input)
            cleaned, errors = post_validate(CONTROLLER_SPECS, flat)
            if cleaned.get(CONF_BINDING_TYPE) == BindingType.ENTITY_STATE.value and (
                not cleaned.get(CONF_BINDING_ENTITY)
            ):
                errors[CONF_BINDING_ENTITY] = "binding_entity_required"
            if not errors:
                self._data = cleaned
                existing_order = (
                    list(subentry.data.get(CONF_SCENE_ORDER) or []) if subentry else []
                )
                known = {s["value"] for s in scene_options(entry)}
                # Drop positions whose scene has since been deleted.
                self._order = [s for s in existing_order if s in known]
                return await self.async_step_order()

        existing = dict(subentry.data) if subentry else None
        return self.async_show_form(
            step_id="reconfigure" if subentry else "user",
            data_schema=build_schema(
                CONTROLLER_SPECS,
                user_input or existing,
                options={"zones": zone_options(entry)},
            ),
            errors=errors,
        )

    # -- the ordering loop -------------------------------------------------

    def _order_summary(self) -> str:
        entry = self._get_entry()
        names = {s["value"]: s["label"] for s in scene_options(entry)}
        adaptive = self._data.get(CONF_ADAPTIVE_POSITION, "first")
        lines: list[str] = []
        if adaptive == "first":
            lines.append("1. Adaptive")
        for index, scene_id in enumerate(self._order, start=len(lines) + 1):
            lines.append(f"{index}. {names.get(scene_id, scene_id)}")
        if adaptive == "last":
            lines.append(f"{len(lines) + 1}. Adaptive")
        if self._data.get(CONF_OFF_AT_END):
            lines.append(f"{len(lines) + 1}. Off")
        return "\n".join(lines) or "(nothing yet)"

    async def async_step_order(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = ["add_step", "finish"]
        if self._order:
            options = ["add_step", "move_step", "remove_step", "finish"]
        return self.async_show_menu(
            step_id="order",
            menu_options=options,
            description_placeholders={"order": self._order_summary()},
        )

    async def async_step_add_step(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        available = [
            option
            for option in scene_options(entry)
            if option["value"] not in self._order
        ]
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
            self._order.remove(user_input["scene"])
            return await self.async_step_order()
        return self.async_show_form(
            step_id="remove_step",
            data_schema=vol.Schema({vol.Required("scene"): self._current_selector()}),
            description_placeholders={"order": self._order_summary()},
        )

    async def async_step_move_step(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            scene_id = user_input["scene"]
            position = int(user_input["position"]) - 1
            self._order.remove(scene_id)
            self._order.insert(max(0, min(position, len(self._order))), scene_id)
            return await self.async_step_order()

        return self.async_show_form(
            step_id="move_step",
            data_schema=vol.Schema(
                {
                    vol.Required("scene"): self._current_selector(),
                    vol.Required("position", default=1): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=max(len(self._order), 1),
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            description_placeholders={"order": self._order_summary()},
        )

    def _current_selector(self) -> Any:
        entry = self._get_entry()
        names = {s["value"]: s["label"] for s in scene_options(entry)}
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {
                        "value": scene_id,
                        "label": f"{index}. {names.get(scene_id, scene_id)}",
                    }
                    for index, scene_id in enumerate(self._order, start=1)
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
                sort=False,
            )
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        data = {**self._data, CONF_SCENE_ORDER: self._order}
        title = data[CONF_NAME]
        if self._subentry is None:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(
            self._get_entry(), self._subentry, data=data, title=title
        )
