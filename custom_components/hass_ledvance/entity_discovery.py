"""Register entities added by cloud discovery without reloading the config entry."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CoordinatorDeviceData, LedvanceTuyaCoordinator


def register_discovered_entities(
    coordinator: LedvanceTuyaCoordinator,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    create_for_device: Callable[[str, CoordinatorDeviceData], Iterable[Entity]],
) -> None:
    """Add entities for new devices and for capabilities discovered later.

    Keep registered entity IDs so a temporarily missing device can recover its
    existing entities when it reappears.
    """
    device_schemas: dict[str, tuple[str, list[dict]]] = {}
    registered_ids: set[str] = set()

    @callback
    def add_new_devices() -> None:
        entities: list[Entity] = []
        for dev_id, dev in (coordinator.data or {}).items():
            schema = (dev.device_type, dev.schema)
            if device_schemas.get(dev_id) == schema:
                continue
            for entity in create_for_device(dev_id, dev):
                if (unique_id := entity.unique_id) is not None and unique_id not in registered_ids:
                    registered_ids.add(unique_id)
                    entities.append(entity)
            device_schemas[dev_id] = schema
        if entities:
            async_add_entities(entities)

    add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(add_new_devices))
