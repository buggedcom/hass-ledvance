"""Newly discovered devices must be registered exactly once."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.hass_ledvance.entity_discovery import register_discovered_entities


def test_entity_discovery_adds_late_devices_once():
    first = SimpleNamespace(device_type="light", schema=[{"code": "bright_value"}])
    second = SimpleNamespace(device_type="unknown", schema=[])
    coordinator = MagicMock()
    coordinator.data = {"one": first}
    callbacks = []
    coordinator.async_add_listener.side_effect = callbacks.append
    entry = MagicMock()
    add_entities = MagicMock()
    first_entity = SimpleNamespace(unique_id="one")
    second_entity = SimpleNamespace(unique_id="two")
    entities = {"one": first_entity, "two": second_entity}
    build_entities = MagicMock(
        side_effect=lambda device_id, dev: [entities[device_id]] if dev.device_type == "light" else []
    )

    register_discovered_entities(coordinator, entry, add_entities, build_entities)
    add_entities.assert_called_once_with([first_entity])
    coordinator.data["two"] = second
    callbacks[0]()
    assert add_entities.call_count == 1
    coordinator.data["two"] = SimpleNamespace(device_type="light", schema=first.schema)
    callbacks[0]()
    assert add_entities.call_count == 2
    add_entities.assert_called_with([second_entity])

    del coordinator.data["one"]
    callbacks[0]()
    coordinator.data["one"] = first
    callbacks[0]()
    assert add_entities.call_count == 2
    assert build_entities.call_count == 3
