"""Hub flow, options flow, and the zone subentry flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.better_lighting.const import (
    DOMAIN,
    HUB_SPECS,
    ZONE_SPECS,
    SubentryType,
)
from tests.conftest import (
    MemberLight,
    form_input,
    hub_entry,
    setup_hub,
    setup_members,
)

HUB_INPUT = form_input(HUB_SPECS)
ZONE_INPUT = form_input(ZONE_SPECS, name="Living Room", lights=["light.three"])


async def finish_zone(hass: HomeAssistant, result):
    """Walk a zone flow from its menu to the end without touching scenes."""
    assert result["step_id"] == "menu"
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )


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
    # The defaults now sit behind a menu that also holds the colour presets.
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**HUB_INPUT, "min_brightness_pct": 12}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["min_brightness_pct"] == 12


async def test_colour_presets_are_named_once_and_reused(
    hass: HomeAssistant,
) -> None:
    """The practical answer to a config flow having no colour wheel."""
    entry = await setup_hub(hass, hub_entry())

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "presets"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_preset"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "TV orange", "color_format": "rgb_color"}
    )
    assert result["step_id"] == "preset_color"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"rgb_color": [255, 140, 40]}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "init"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    presets = entry.options["color_presets"]
    assert presets == [
        {"name": "TV orange", "color_format": "rgb_color", "rgb_color": [255, 140, 40]}
    ]


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
    # The room's settings are followed by its own menu, where its scenes live.
    result = await finish_zone(hass, result)
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
    result = await finish_zone(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[subentry_id].data["icon"] == "mdi:stove"
