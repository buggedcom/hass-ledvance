"""Number platform for Ledvance/Tuya integration.

Provides per-outlet countdown timers on socket strip devices.
Setting a countdown to 0 cancels it; any positive value (seconds)
schedules the outlet to turn off after that duration.
"""

from __future__ import annotations

import logging
from math import isfinite

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DPS_COUNTDOWN_PREFIX
from .coordinator import CoordinatorDeviceData, LedvanceDeviceEntity, LedvanceTuyaCoordinator
from .entity_discovery import register_discovered_entities
from .local_control import async_send_and_update
from .schema_parser import get_integer_range

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ledvance/Tuya number entities."""
    coordinator: LedvanceTuyaCoordinator = hass.data[DOMAIN][entry.entry_id]

    def build_entities(dev_id: str, dev: CoordinatorDeviceData) -> list[NumberEntity]:
        entities: list[NumberEntity] = []
        if dev.device_type != "socket_strip":
            return []

        # Find all countdown_N DPS in the schema, ordered by outlet number
        countdown_items = sorted(
            (
                item
                for item in dev.schema
                if (item.get("code", "")).startswith(DPS_COUNTDOWN_PREFIX)
                and item["code"][len(DPS_COUNTDOWN_PREFIX) :].isdecimal()
            ),
            key=lambda i: int(i["code"][len(DPS_COUNTDOWN_PREFIX) :]),
        )

        for item in countdown_items:
            code = item["code"]
            outlet_num = code[len(DPS_COUNTDOWN_PREFIX) :]
            entities.append(
                LedvanceTuyaCountdown(
                    coordinator, entry, dev_id, code, f"Outlet {outlet_num} Countdown"
                )
            )

        return entities

    register_discovered_entities(coordinator, entry, async_add_entities, build_entities)


class LedvanceTuyaCountdown(LedvanceDeviceEntity, NumberEntity):
    """Per-outlet countdown timer (seconds) for a Ledvance/Tuya socket strip."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1.0

    def __init__(
        self,
        coordinator: LedvanceTuyaCoordinator,
        entry: ConfigEntry,
        device_id: str,
        dps_code: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._dps_code = dps_code
        self._attr_name = label
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_{dps_code}"

        dev = coordinator.data[device_id]
        self._dps: str | None = dev.dps_map.get(dps_code)

        lo, hi = get_integer_range(dev.schema, dps_code)
        self._attr_native_min_value = float(lo)
        self._attr_native_max_value = float(hi)

    @property
    def native_value(self) -> float | None:
        if self._dps is None:
            return None
        raw = self._state_dps.get(self._dps)
        if raw is None:
            return None
        try:
            value = float(raw)
            return value if isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        if self._dps is None:
            return
        dps = {self._dps: int(value)}
        await async_send_and_update(self.hass, self.coordinator, self._device_id, dps)
