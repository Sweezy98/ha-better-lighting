"""Hub flow, options flow, and the zone subentry flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.better_lighting.const import DOMAIN, SubentryType
from tests.conftest import MemberLight, hub_entry, setup_hub, setup_members

HUB_INPUT = {
    "interval": 90,
    "transition": 45,
    "min_brightness_pct": 1,
    "max_brightness_pct": 100,
    "min_color_temp_k": 2000,
    "max_color_temp_k": 5500,
    "brightness_mode": "tanh",
    "advanced": {},
}

# The frontend always submits every section, so the tests do too.
ZONE_INPUT = {
    "name": "Living Room",
    "lights": ["light.three"],
    "icon": "mdi:lightbulb-group",
    "group": {},
    "adaptive": {},
    "night": {},
}


async def test_creates_the_hub(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], HUB_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Better Lighting"
    # Global defaults live in options, so they can be edited without a migration.
    assert result["options"]["brightness_mode"] == "tanh"
    assert result["options"]["interval"] == 90


async def test_only_one_hub_allowed(hass: HomeAssistant) -> None:
    await setup_hub(hass, hub_entry())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_updates_defaults(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass, hub_entry())

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**HUB_INPUT, "min_brightness_pct": 12}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["min_brightness_pct"] == 12


async def test_adds_a_zone_subentry(hass: HomeAssistant) -> None:
    await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
    entry = await setup_hub(hass, hub_entry())

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SubentryType.ZONE.value),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], ZONE_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room"

    titles = {sub.title for sub in entry.subentries.values()}
    assert titles == {"Kitchen", "Living Room"}


async def test_rejects_a_light_already_in_another_zone(hass: HomeAssistant) -> None:
    """The one-light-one-zone invariant the whole architecture rests on."""
    entry = await setup_hub(hass, hub_entry())

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SubentryType.ZONE.value),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**ZONE_INPUT, "lights": ["light.one"]},  # already in Kitchen
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"lights": "light_in_other_zone"}


async def test_rejects_a_zone_with_no_lights(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass, hub_entry())

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SubentryType.ZONE.value),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**ZONE_INPUT, "lights": []}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"lights": "no_lights"}


async def test_reconfigure_keeps_its_own_lights(hass: HomeAssistant) -> None:
    """A zone must not be told its own lights belong to another zone."""
    entry = await setup_hub(hass, hub_entry())
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SubentryType.ZONE.value),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **ZONE_INPUT,
            "name": "Kitchen",
            "lights": ["light.one", "light.two"],
            "icon": "mdi:stove",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[subentry_id].data["icon"] == "mdi:stove"
