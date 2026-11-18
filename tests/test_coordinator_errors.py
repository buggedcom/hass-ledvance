"""Coordinator auth and device polling regression tests."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hass_ledvance.coordinator import LedvanceTuyaCoordinator
from custom_components.hass_ledvance.exceptions import (
    CannotConnect,
    InvalidAuthentication,
    TooManyRequests,
)


@pytest.mark.parametrize("exc", [InvalidAuthentication(), TooManyRequests("limit"), CannotConnect("down")])
async def test_coordinator_classifies_cloud_error(hass, exc):
    api = MagicMock()
    api.sid = "cached"
    api.groups.side_effect = exc
    coordinator = LedvanceTuyaCoordinator(hass, api)
    with (
        patch.object(coordinator, "_scan_lan", return_value={}),
        pytest.raises(
            ConfigEntryAuthFailed if isinstance(exc, InvalidAuthentication) else UpdateFailed
        ),
    ):
        await coordinator._async_update_data()


def test_group_auth_failure_is_not_masked(hass):
    api = MagicMock()
    api.sid = "cached"
    api.groups.return_value = [{"groupId": "123"}]
    api.devices.side_effect = InvalidAuthentication()
    coordinator = LedvanceTuyaCoordinator(hass, api)
    with pytest.raises(InvalidAuthentication):
        coordinator._fetch_all_devices()
