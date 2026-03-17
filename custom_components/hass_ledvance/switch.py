"""Switch platform for Ledvance/Tuya integration.

Covers three entity types:
  - LedvanceTuyaSwitch      — single-outlet smart plug / in-line switch
  - LedvanceTuyaOutlet      — individual outlet on a power strip (socket_strip)
  - LedvanceTuyaChildLock   — child-lock toggle on a power strip
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DPS_CHILD_LOCK
from .coordinator import CoordinatorDeviceData, LedvanceTuyaCoordinator
from .local_control import async_send_command
from .schema_parser import get_socket_outlet_dps

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ledvance/Tuya switch entities."""
    coordinator: LedvanceTuyaCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []

    for dev_id, dev in coordinator.data.items():
        if dev.device_type == "switch":
            entities.append(LedvanceTuyaSwitch(coordinator, entry, dev_id))

        elif dev.device_type == "socket_strip":
            # One switch per outlet / USB port / master
            for dps_code, label in get_socket_outlet_dps(dev.schema):
                entities.append(
                    LedvanceTuyaOutlet(coordinator, entry, dev_id, dps_code, label)
                )
            # Child lock (if device supports it)
            if any(item.get("code") == DPS_CHILD_LOCK for item in dev.schema):
                entities.append(LedvanceTuyaChildLock(coordinator, entry, dev_id))

    _LOGGER.debug("Setting up %d switch entities", len(entities))
    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base mixin — shared device_info / _device_data helpers
# ---------------------------------------------------------------------------

class _LedvanceDeviceMixin(CoordinatorEntity[LedvanceTuyaCoordinator]):
    """Shared helpers for all Ledvance/Tuya switch entity classes."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LedvanceTuyaCoordinator,
        entry: ConfigEntry,
        device_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._entry = entry
        dev = coordinator.data[device_id]
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": dev.name,
            "manufacturer": "Ledvance / Tuya",
        }

    @property
    def _device_data(self) -> CoordinatorDeviceData:
        return self.coordinator.data[self._device_id]

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._device_id in self.coordinator.data
            and self._device_data.is_online
        )


# ---------------------------------------------------------------------------
# Single-outlet smart plug / in-line switch
# ---------------------------------------------------------------------------

class LedvanceTuyaSwitch(_LedvanceDeviceMixin, SwitchEntity):
    """Representation of a single-outlet Ledvance/Tuya switch or plug."""

    _attr_name = None  # uses device name
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(
        self,
        coordinator: LedvanceTuyaCoordinator,
        entry: ConfigEntry,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, entry, device_id)
        self._attr_unique_id = f"{entry.entry_id}_{device_id}"
        dev = coordinator.data[device_id]

        # First switch_* code in the schema
        self._switch_dps: str | None = None
        for item in dev.schema:
            code = item.get("code", "")
            if code.startswith("switch"):
                self._switch_dps = dev.dps_map.get(code)
                break

    @property
    def is_on(self) -> bool | None:
        if self._switch_dps is None:
            return None
        return bool(self._device_data.dps.get(self._switch_dps))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._switch_dps is None:
            return
        await async_send_command(
            self.hass, self.coordinator.api, self._device_data,
            {self._switch_dps: True},
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._switch_dps is None:
            return
        await async_send_command(
            self.hass, self.coordinator.api, self._device_data,
            {self._switch_dps: False},
        )
        await self.coordinator.async_request_refresh()


# ---------------------------------------------------------------------------
# Individual outlet on a power strip
# ---------------------------------------------------------------------------

class LedvanceTuyaOutlet(_LedvanceDeviceMixin, SwitchEntity):
    """One controllable outlet / USB port on a Ledvance/Tuya socket strip."""

    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(
        self,
        coordinator: LedvanceTuyaCoordinator,
        entry: ConfigEntry,
        device_id: str,
        dps_code: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, entry, device_id)
        self._dps_code = dps_code
        self._attr_name = label
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_{dps_code}"
        dev = coordinator.data[device_id]
        self._switch_dps: str | None = dev.dps_map.get(dps_code)

    @property
    def is_on(self) -> bool | None:
        if self._switch_dps is None:
            return None
        return bool(self._device_data.dps.get(self._switch_dps))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._switch_dps is None:
            return
        await async_send_command(
            self.hass, self.coordinator.api, self._device_data,
            {self._switch_dps: True},
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._switch_dps is None:
            return
        await async_send_command(
            self.hass, self.coordinator.api, self._device_data,
            {self._switch_dps: False},
        )
        await self.coordinator.async_request_refresh()


# ---------------------------------------------------------------------------
# Child lock
# ---------------------------------------------------------------------------

class LedvanceTuyaChildLock(_LedvanceDeviceMixin, SwitchEntity):
    """Child-lock toggle for a Ledvance/Tuya socket strip."""

    _attr_name = "Child Lock"
    _attr_icon = "mdi:lock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: LedvanceTuyaCoordinator,
        entry: ConfigEntry,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, entry, device_id)
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_child_lock"
        dev = coordinator.data[device_id]
        self._lock_dps: str | None = dev.dps_map.get(DPS_CHILD_LOCK)

    @property
    def is_on(self) -> bool | None:
        if self._lock_dps is None:
            return None
        return bool(self._device_data.dps.get(self._lock_dps))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._lock_dps is None:
            return
        await async_send_command(
            self.hass, self.coordinator.api, self._device_data,
            {self._lock_dps: True},
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._lock_dps is None:
            return
        await async_send_command(
            self.hass, self.coordinator.api, self._device_data,
            {self._lock_dps: False},
        )
        await self.coordinator.async_request_refresh()
