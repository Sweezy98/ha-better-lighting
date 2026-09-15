"""Entry setup, unload, and the reload-on-change behaviour."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.better_lighting.const import DOMAIN
from tests.conftest import (
    MemberLight,
    hub_entry,
    setup_hub,
    setup_members,
    zone_subentry,
)


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
    entry = await setup_hub(hass, hub_entry())

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("light.kitchen") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_runtime_holds_parsed_config(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass, hub_entry())
    runtime = entry.runtime_data

    assert len(runtime.zones) == 1
    zone = next(iter(runtime.zones.values()))
    assert zone.name == "Kitchen"
    assert zone.lights == ("light.one", "light.two")
    assert zone.slug == "kitchen"
    # Unset hub options fall back to the documented defaults.
    assert runtime.hub.interval == 90
    assert runtime.hub.max_color_temp_k == 5500


async def test_each_zone_gets_its_own_device(hass: HomeAssistant) -> None:
    await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
    entry = await setup_hub(hass, hub_entry())

    subentry_id = next(iter(entry.runtime_data.zones))
    entity = er.async_get(hass).async_get("light.kitchen")
    assert entity is not None
    # Binding to the subentry is what lets HA clean up on deletion.
    assert entity.config_subentry_id == subentry_id

    # Resolve the device through the entity rather than by identifier: the
    # identifier lookup helpers have changed shape across HA releases, and
    # this route works on every version the integration supports.
    assert entity.device_id is not None
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device is not None
    assert device.name == "Kitchen"
    assert (DOMAIN, subentry_id) in device.identifiers


async def test_editing_a_subentry_reloads(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass, hub_entry())
    subentry = next(iter(entry.subentries.values()))

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload"
    ) as mock_reload:
        hass.config_entries.async_update_subentry(
            entry, subentry, data={**subentry.data, "icon": "mdi:stove"}
        )
        await hass.async_block_till_done()

    # Verified against HA 2026.9.2: subentry mutations fire the entry's
    # update listeners, so no explicit reload call is needed in the flows.
    assert mock_reload.called


async def test_a_no_op_update_does_not_reload(hass: HomeAssistant) -> None:
    """Guards against reload storms when something re-saves identical config."""
    entry = await setup_hub(hass, hub_entry())

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload"
    ) as mock_reload:
        hass.config_entries.async_update_entry(entry, options=dict(entry.options))
        await hass.async_block_till_done()

    assert not mock_reload.called


async def test_removing_a_zone_removes_its_entity(hass: HomeAssistant) -> None:
    await setup_members(hass, [MemberLight("One"), MemberLight("Two")])
    entry = await setup_hub(hass, hub_entry())
    subentry_id = next(iter(entry.subentries))

    hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()

    assert hass.states.get("light.kitchen") is None
    assert er.async_get(hass).async_get("light.kitchen") is None


async def test_two_zones_are_independent(hass: HomeAssistant) -> None:
    await setup_members(
        hass, [MemberLight("One"), MemberLight("Two"), MemberLight("Three")]
    )
    entry = hub_entry(
        subentries_data=[
            zone_subentry("Kitchen", ["light.one", "light.two"]),
            zone_subentry("Hallway", ["light.three"]),
        ]
    )
    await setup_hub(hass, entry)

    assert hass.states.get("light.kitchen") is not None
    assert hass.states.get("light.hallway") is not None

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.hallway"}, blocking=True
    )
    await hass.async_block_till_done()

    # A light belongs to exactly one zone, so acting on one cannot touch another.
    assert hass.states.get("light.three").state == "on"
    assert hass.states.get("light.one").state == "off"
    assert hass.states.get("light.kitchen").state == "off"
