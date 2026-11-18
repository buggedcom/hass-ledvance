"""Exercise Ledvance entities and coordinator lifecycle through Home Assistant."""

import asyncio
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hass_ledvance.api import DeviceInfo, TuyaAPI
from custom_components.hass_ledvance.const import CONF_REGION, DOMAIN
from custom_components.hass_ledvance.coordinator import LedvanceTuyaCoordinator

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def device(dev_id, schema, dps):
    return DeviceInfo(dev_id, dev_id, dev_id, "", True, "placeholder", "", schema, dps)


async def test_platforms_recover_on_discovery_and_stop_after_unload(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_EMAIL: "account@example.invalid",
        CONF_PASSWORD: "placeholder",
        CONF_REGION: "EU",
        "_sid": "test-session",
    })
    entry.add_to_hass(hass)

    lamp = device("Lamp", [
        {"id": 1, "code": "switch_led"},
        {"id": 2, "code": "bright_value"},
    ], {"1": True, "2": 100})
    strip = device("Strip", [
        {"id": 1, "code": "switch_1"},
        {"id": 2, "code": "switch_2"},
        {"id": 3, "code": "countdown_1"},
        {"id": 4, "code": "cur_current", "property": {"scale": 0, "unit": "mA"}},
        {"id": 5, "code": "cur_voltage", "property": {"scale": 1, "unit": "mV"}},
        {"id": 6, "code": "cur_power"},
        {"id": 7, "code": "total_forward_energy", "property": '{"scale": 3, "unit": "kwh"}'},
    ], {"1": True, "2": False, "3": 25, "4": 850, "5": 2300000, "6": 850, "7": 1550})
    incomplete = device("Incomplete", None, {})
    known_plug = device("KnownPlug", None, {})
    known_plug.product_id = "pq860vo9ib50jhud"
    malformed = device("Malformed", 123, {})
    devices = [lamp, strip, incomplete, known_plug, malformed]
    api = MagicMock(spec=TuyaAPI)
    api.sid = "test-session"
    api.groups.return_value = [{"groupId": "home"}]
    api.devices.side_effect = lambda _group: list(devices)
    api.get_dps.side_effect = lambda dev_id: next(dev.dps for dev in devices if dev.dev_id == dev_id)

    registry = er.async_get(hass)
    legacy_key = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_Strip_local_key", config_entry=entry
    )
    with (
        patch.object(TuyaAPI, "for_region", return_value=api),
        patch.object(LedvanceTuyaCoordinator, "_scan_lan", return_value={}),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        coordinator = entry.runtime_data
        assert set(coordinator.data) == {"Lamp", "Strip", "Incomplete", "KnownPlug"}
        registered = er.async_entries_for_config_entry(registry, entry.entry_id)
        current = next(entity for entity in registered if entity.unique_id.endswith("_Strip_current"))
        assert float(hass.states.get(current.entity_id).state) == 0.85
        assert hass.states.get(current.entity_id).attributes["unit_of_measurement"] == "A"
        assert not any(entity.unique_id.endswith("_KnownPlug") for entity in registered)
        for suffix, value in (("voltage", 230.0), ("power", 85.0), ("energy", 1.55)):
            sensor = next(entity for entity in registered if entity.unique_id.endswith(f"_Strip_{suffix}"))
            assert float(hass.states.get(sensor.entity_id).state) == value

        issue = ir.async_get(hass).async_get_issue(DOMAIN, f"legacy_local_key_{entry.entry_id}")
        assert issue.translation_placeholders == {"entity_ids": legacy_key.entity_id}

        # LAN and optimistic updates use the same path and keep the pending cloud timer.
        scheduled_refresh = coordinator._unsub_refresh
        assert scheduled_refresh is not None
        coordinator.async_optimistic_update("Strip", {"4": 900})
        assert coordinator._unsub_refresh is scheduled_refresh
        assert float(hass.states.get(current.entity_id).state) == 0.9

        incomplete.schema = lamp.schema
        incomplete.dps = lamp.dps
        known_plug.schema = [{"id": 1, "code": "switch_1"}]
        known_plug.dps = {"1": True}
        devices.remove(strip)
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        strip_states = [
            hass.states.get(entity.entity_id)
            for entity in registered
            if entity.device_id == current.device_id and entity.disabled_by is None
        ]
        assert strip_states and all(state.state == STATE_UNAVAILABLE for state in strip_states)
        updated_registry = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert any(e.domain == "light" and e.unique_id.endswith("_Incomplete") for e in updated_registry)
        assert any(e.domain == "switch" and e.unique_id.endswith("_KnownPlug") for e in updated_registry)

        devices.append(strip)
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert float(hass.states.get(current.entity_id).state) == 0.85

        monitor = coordinator._lan_monitor_task
        started = asyncio.Event()
        finish_fetch = Event()
        original_fetch = coordinator._fetch_all_devices

        def delayed_fetch():
            hass.loop.call_soon_threadsafe(started.set)
            assert finish_fetch.wait(timeout=5)
            return original_fetch()

        with patch.object(coordinator, "_fetch_all_devices", new=delayed_fetch):
            refresh = asyncio.create_task(coordinator.async_refresh())
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                assert await hass.config_entries.async_unload(entry.entry_id)
            finally:
                finish_fetch.set()
                await refresh

        assert coordinator._lan_monitor_task is monitor
        assert monitor.done()
        assert coordinator._shutdown_requested
        assert coordinator._unsub_refresh is None
