"""Preserve manual area assignments and clears through refresh and reload."""

from types import SimpleNamespace

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hass_ledvance import _async_assign_areas
from custom_components.hass_ledvance.const import DOMAIN


@pytest.mark.parametrize("manual_change", ["clear", "move"])
async def test_persists_manual_area_changes(hass, manual_change):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    areas = ar.async_get(hass)
    office = areas.async_create("Office")
    kitchen = areas.async_create("Kitchen")
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "dev")})
    snapshot = {"dev": SimpleNamespace(room_name="Office", name="Light")}

    _async_assign_areas(hass, SimpleNamespace(data=snapshot), entry)
    assert registry.async_get(device.id).area_id == office.id

    area = kitchen.id if manual_change == "move" else None
    registry.async_update_device(device.id, area_id=area)
    for _ in range(3):
        _async_assign_areas(hass, SimpleNamespace(data=snapshot), entry)
        assert registry.async_get(device.id).area_id == area

    assert entry.data["_auto_area_assignments"]["dev"] is None


async def test_preserves_preexisting_area_and_moves_automatically_assigned_devices(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    areas = ar.async_get(hass)
    office = areas.async_create("Office")
    kitchen = areas.async_create("Kitchen")
    registry = dr.async_get(hass)
    manual = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "manual")})
    automatic = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "auto")})
    registry.async_update_device(manual.id, area_id=kitchen.id)
    coordinator = SimpleNamespace(data={
        "manual": SimpleNamespace(room_name="Office", name="Manual"),
        "auto": SimpleNamespace(room_name="Office", name="Automatic"),
    })

    _async_assign_areas(hass, coordinator, entry)
    assert registry.async_get(manual.id).area_id == kitchen.id
    assert registry.async_get(automatic.id).area_id == office.id

    coordinator.data["auto"].room_name = "Kitchen"
    _async_assign_areas(hass, coordinator, entry)
    assert registry.async_get(automatic.id).area_id == kitchen.id
