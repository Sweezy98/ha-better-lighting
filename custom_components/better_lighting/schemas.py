"""Build config-flow forms from ``FieldSpec`` tables.

One function builds every form in the integration, and one flattens the result
back into a flat dict.  Non-basic sections render as collapsed groups, which
keeps the common case (name the zone, pick the lights) to a short form while
still exposing every knob.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.data_entry_flow import SectionConfig, section
from homeassistant.helpers import selector
from homeassistant.helpers.selector import SelectOptionDict

from .const import FieldSpec, Section

_LOGGER = logging.getLogger(__name__)


def build_schema(
    specs: tuple[FieldSpec, ...],
    values: Mapping[str, Any] | None = None,
    *,
    include: tuple[Section, ...] | None = None,
    options: Mapping[str, list[SelectOptionDict]] | None = None,
) -> vol.Schema:
    """Render ``specs`` as a voluptuous schema, pre-filled from ``values``.

    ``include`` restricts which sections are offered, so a milestone can ship a
    subset of a table without touching it. ``options`` supplies the choices for
    fields whose possible values only exist at runtime -- which scenes have been
    defined, for instance.
    """
    current = dict(values or {})
    basic: dict[Any, Any] = {}
    grouped: dict[Section, dict[Any, Any]] = {}

    for spec in specs:
        if include is not None and spec.section not in include:
            continue

        default = spec.as_default(current)
        widget = spec.selector
        if spec.options_key is not None:
            choices = (options or {}).get(spec.options_key) or []
            if not choices:
                # Nothing to choose from yet. Offering an empty dropdown just
                # produces a dead control, so the field is left out and the
                # form explains why instead.
                continue
            # Rebuild the widget around the runtime choices, but carry the
            # spec's own settings across. Dropping them silently turned every
            # multi-select whose options are runtime-supplied into a
            # single-select, which the form then rejected.
            original = getattr(spec.selector, "config", {}) or {}
            widget = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=choices,
                    multiple=bool(original.get("multiple", False)),
                    mode=original.get(
                        "mode", selector.SelectSelectorMode.DROPDOWN
                    ),
                    sort=False,
                )
            )
        marker = vol.Required if spec.required else vol.Optional
        # A field with no default must not carry `default=None`: voluptuous
        # would happily validate None against the selector and fail.
        key = marker(spec.key) if default is None else marker(spec.key, default=default)

        target = (
            basic
            if spec.section is Section.BASIC
            else grouped.setdefault(spec.section, {})
        )
        target[key] = widget

    schema: dict[Any, Any] = dict(basic)
    for sec, fields in grouped.items():
        schema[vol.Required(sec.value)] = section(
            vol.Schema(fields), SectionConfig(collapsed=True)
        )
    return vol.Schema(schema)


def flatten_sections(
    specs: tuple[FieldSpec, ...], user_input: Mapping[str, Any]
) -> dict[str, Any]:
    """Collapse the nested section dicts the form returns back into one level."""
    section_names = {
        spec.section.value for spec in specs if spec.section is not Section.BASIC
    }
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in section_names and isinstance(value, Mapping):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def post_validate(
    specs: tuple[FieldSpec, ...], user_input: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Apply the validators a selector cannot express.

    Returns ``(cleaned, errors)``. ``errors`` is keyed by field name, in the
    shape a config flow's ``errors`` argument expects, so an invalid value
    highlights the field that caused it rather than failing the whole form.
    """
    cleaned = dict(user_input)
    errors: dict[str, str] = {}

    by_key = {spec.key: spec for spec in specs}
    for key, value in user_input.items():
        spec = by_key.get(key)
        if spec is None or spec.validator is None or value is None:
            continue
        try:
            validated = spec.validator(value)
        except (vol.Invalid, ValueError) as err:
            _LOGGER.debug("Validation failed for %s=%r: %s", key, value, err)
            errors[key] = "invalid_value"
            continue
        cleaned[key] = spec.coerce(validated) if spec.coerce else validated

    return cleaned, errors


def validate_range_pair(
    values: Mapping[str, Any], low_key: str, high_key: str, error: str
) -> dict[str, str]:
    """Flag a min/max pair that is the wrong way round.

    Deliberately *not* applied to the adaptive brightness and colour-temp
    ranges: an inverted range there is a supported way to express a reversed
    schedule, and ``util.clamp`` sorts its bounds to accommodate it.
    """
    low, high = values.get(low_key), values.get(high_key)
    if low is None or high is None or low <= high:
        return {}
    return {low_key: error, high_key: error}
