"""Sensor platform for Ledvance/Tuya integration.

Diagnostic sensors show connection information. Power monitoring sensors show
current, voltage, power, and energy on supported outlets.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_conversion import (
    BaseUnitConverter,
    ElectricCurrentConverter,
    ElectricPotentialConverter,
    EnergyConverter,
    PowerConverter,
)

from .const import (
    DOMAIN,
    DPS_CUR_CURRENT,
    DPS_CUR_POWER,
    DPS_CUR_VOLTAGE,
    DPS_TOTAL_ENERGY,
)
from .coordinator import CoordinatorDeviceData, LedvanceDeviceEntity, LedvanceTuyaCoordinator
from .entity_discovery import register_discovered_entities
from .schema_parser import get_numeric_scale, get_numeric_unit

_LOGGER = logging.getLogger(__name__)

_UNIT_ALIASES = {"a": "A", "ma": "mA", "v": "V", "mv": "mV", "w": "W", "kw": "kW", "wh": "Wh", "kwh": "kWh"}


# ---------------------------------------------------------------------------
# Diagnostic sensors (text, all device types)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class LedvanceDiagSensorDescription(SensorEntityDescription):
    """Diagnostic sensor with a data-accessor callback."""

    value_fn: Callable[[CoordinatorDeviceData], str | None]


DIAG_SENSOR_DESCRIPTIONS: tuple[LedvanceDiagSensorDescription, ...] = (
    # Device ID, MAC and firmware version are now surfaced in the HA device info
    # panel (serial number, connections, sw_version) — no need to duplicate here.
    LedvanceDiagSensorDescription(
        key="lan_ip_address",
        name="LAN IP Address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.lan_ip,
    ),
    LedvanceDiagSensorDescription(
        key="cloud_ip_address",
        name="Cloud IP Address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.ip,
    ),
    LedvanceDiagSensorDescription(
        key="protocol_version",
        name="Protocol Version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.version,
    ),
)


# ---------------------------------------------------------------------------
# Power monitoring sensors (socket_strip devices only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class LedvancePowerSensorDescription(SensorEntityDescription):
    """Power-monitoring sensor: DPS code + optional default scale divisor."""

    dps_code: str
    unit_converter: type[BaseUnitConverter]
    default_scale: float = 1.0  # fallback if schema has no 'scale' property


POWER_SENSOR_DESCRIPTIONS: tuple[LedvancePowerSensorDescription, ...] = (
    LedvancePowerSensorDescription(
        key="current",
        name="Current",
        dps_code=DPS_CUR_CURRENT,
        unit_converter=ElectricCurrentConverter,
        default_scale=1000.0,  # Tuya typically reports mA
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=3,
    ),
    LedvancePowerSensorDescription(
        key="voltage",
        name="Voltage",
        dps_code=DPS_CUR_VOLTAGE,
        unit_converter=ElectricPotentialConverter,
        default_scale=10.0,  # Tuya typically reports 0.1 V steps
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
    ),
    LedvancePowerSensorDescription(
        key="power",
        name="Power",
        dps_code=DPS_CUR_POWER,
        unit_converter=PowerConverter,
        default_scale=10.0,  # Tuya typically reports 0.1 W steps
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=1,
    ),
    LedvancePowerSensorDescription(
        key="energy",
        name="Energy",
        dps_code=DPS_TOTAL_ENERGY,
        unit_converter=EnergyConverter,
        default_scale=1000.0,  # Tuya typically reports Wh; HA wants kWh
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
    ),
)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ledvance/Tuya sensor entities."""
    coordinator: LedvanceTuyaCoordinator = hass.data[DOMAIN][entry.entry_id]

    def build_entities(dev_id: str, dev: CoordinatorDeviceData) -> list[SensorEntity]:
        entities: list[SensorEntity] = []
        # Diagnostic sensors for every device
        for desc in DIAG_SENSOR_DESCRIPTIONS:
            entities.append(LedvanceDiagSensor(coordinator, entry, dev_id, desc))

        # Power monitoring sensors for socket strips and smart plugs with power DPS
        if dev.device_type in ("socket_strip", "switch"):
            dps_codes_in_schema = {item.get("code") for item in dev.schema}
            for desc in POWER_SENSOR_DESCRIPTIONS:
                if desc.dps_code in dps_codes_in_schema:
                    entities.append(LedvancePowerSensor(coordinator, entry, dev_id, desc))

        return entities

    register_discovered_entities(coordinator, entry, async_add_entities, build_entities)


# ---------------------------------------------------------------------------
# Entity classes
# ---------------------------------------------------------------------------


class LedvanceDiagSensor(LedvanceDeviceEntity, SensorEntity):
    """A diagnostic (text) sensor for any Ledvance/Tuya device."""

    entity_description: LedvanceDiagSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LedvanceTuyaCoordinator,
        entry: ConfigEntry,
        device_id: str,
        description: LedvanceDiagSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_{description.key}"

    @property
    def native_value(self) -> str | None:
        device = self._device_data
        return self.entity_description.value_fn(device) if device is not None else None


class LedvancePowerSensor(LedvanceDeviceEntity, SensorEntity):
    """A power-monitoring sensor on a Ledvance/Tuya socket strip."""

    entity_description: LedvancePowerSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LedvanceTuyaCoordinator,
        entry: ConfigEntry,
        device_id: str,
        description: LedvancePowerSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_{description.key}"

        dev = coordinator.data[device_id]
        self._dps: str | None = dev.dps_map.get(description.dps_code)
        schema_scale = get_numeric_scale(dev.schema, description.dps_code)
        self._divisor = description.default_scale if schema_scale is None else schema_scale
        self._unit_factor = 1.0
        if schema_scale is not None:
            reported_unit = get_numeric_unit(dev.schema, description.dps_code)
            source_unit = _UNIT_ALIASES.get(reported_unit.casefold(), reported_unit)
            converter = description.unit_converter
            if source_unit in converter.VALID_UNITS:
                self._unit_factor = converter.convert(
                    1.0, source_unit, description.native_unit_of_measurement
                )

    @property
    def native_value(self) -> float | None:
        if self._dps is None:
            return None
        raw = self._state_dps.get(self._dps)
        if raw is None:
            return None
        try:
            value = float(raw) / self._divisor * self._unit_factor
            return round(value, 6) if isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            return None
