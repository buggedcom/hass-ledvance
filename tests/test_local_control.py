"""Tests for custom_components.hass_ledvance.local_control."""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from custom_components.hass_ledvance.local_control import async_send_command


# ---------------------------------------------------------------------------
# Minimal CoordinatorDeviceData stand-in
# ---------------------------------------------------------------------------

@dataclass
class _FakeDevice:
    device_id: str = "dev001"
    gateway_id: str | None = None
    name: str = "Test Device"
    lan_ip: str | None = None
    local_key: str | None = None
    version: str | None = None
    dps: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tinytuya_mock(success: bool = True):
    """Return a mock tinytuya module whose OutletDevice.set_status behaves as requested."""
    device_mock = MagicMock()
    device_mock.set_status.return_value = {} if success else {"Error": "timeout"}
    tinytuya_mock = MagicMock()
    tinytuya_mock.OutletDevice.return_value = device_mock
    return tinytuya_mock, device_mock


# ---------------------------------------------------------------------------
# Local control path
# ---------------------------------------------------------------------------

class TestLocalControlSuccess:
    async def test_returns_true_when_local_succeeds(self, hass, mock_api):
        device = _FakeDevice(lan_ip="192.168.1.50", local_key="abc", version="3.3")
        tinytuya_mock, _ = _make_tinytuya_mock(success=True)

        with patch.dict("sys.modules", {"tinytuya": tinytuya_mock}):
            result = await async_send_command(hass, mock_api, device, {"1": True})

        assert result is True

    async def test_does_not_call_cloud_when_local_succeeds(self, hass, mock_api):
        device = _FakeDevice(lan_ip="192.168.1.50", local_key="abc", version="3.3")
        tinytuya_mock, _ = _make_tinytuya_mock(success=True)

        with patch.dict("sys.modules", {"tinytuya": tinytuya_mock}):
            await async_send_command(hass, mock_api, device, {"1": True})

        mock_api.set_dps.assert_not_called()

    async def test_sets_correct_socket_timeout(self, hass, mock_api):
        device = _FakeDevice(lan_ip="192.168.1.50", local_key="abc", version="3.3")
        tinytuya_mock, device_mock = _make_tinytuya_mock(success=True)

        with patch.dict("sys.modules", {"tinytuya": tinytuya_mock}):
            await async_send_command(hass, mock_api, device, {"1": True})

        device_mock.set_socketTimeout.assert_called_once_with(3)


# ---------------------------------------------------------------------------
# Local control failure → cloud fallback
# ---------------------------------------------------------------------------

class TestLocalControlFallback:
    async def test_falls_back_to_cloud_on_tinytuya_error_dict(self, hass, mock_api):
        device = _FakeDevice(lan_ip="192.168.1.50", local_key="abc", version="3.3")
        tinytuya_mock, _ = _make_tinytuya_mock(success=False)

        with patch.dict("sys.modules", {"tinytuya": tinytuya_mock}):
            result = await async_send_command(hass, mock_api, device, {"1": True})

        assert result is True
        mock_api.set_dps.assert_called_once_with("dev001", None, {"1": True})

    async def test_falls_back_to_cloud_on_exception(self, hass, mock_api):
        device = _FakeDevice(lan_ip="192.168.1.50", local_key="abc", version="3.3")
        tinytuya_mock = MagicMock()
        tinytuya_mock.OutletDevice.side_effect = OSError("connection refused")

        with patch.dict("sys.modules", {"tinytuya": tinytuya_mock}):
            result = await async_send_command(hass, mock_api, device, {"1": False})

        assert result is True
        mock_api.set_dps.assert_called_once()

    async def test_falls_back_when_tinytuya_not_installed(self, hass, mock_api):
        """If tinytuya isn't importable, go straight to cloud."""
        device = _FakeDevice(lan_ip="192.168.1.50", local_key="abc", version="3.3")

        with patch.dict("sys.modules", {"tinytuya": None}):
            result = await async_send_command(hass, mock_api, device, {"1": True})

        assert result is True
        mock_api.set_dps.assert_called_once()


# ---------------------------------------------------------------------------
# No LAN IP → goes straight to cloud
# ---------------------------------------------------------------------------

class TestNoLanIp:
    async def test_no_lan_ip_uses_cloud_directly(self, hass, mock_api):
        device = _FakeDevice(lan_ip=None, local_key="abc", version="3.3")

        result = await async_send_command(hass, mock_api, device, {"1": True})

        assert result is True
        mock_api.set_dps.assert_called_once_with("dev001", None, {"1": True})

    async def test_no_local_key_uses_cloud_directly(self, hass, mock_api):
        device = _FakeDevice(lan_ip="192.168.1.50", local_key=None, version="3.3")

        result = await async_send_command(hass, mock_api, device, {"1": True})

        assert result is True
        mock_api.set_dps.assert_called_once()

    async def test_no_version_uses_cloud_directly(self, hass, mock_api):
        device = _FakeDevice(lan_ip="192.168.1.50", local_key="abc", version=None)

        result = await async_send_command(hass, mock_api, device, {"1": True})

        assert result is True
        mock_api.set_dps.assert_called_once()


# ---------------------------------------------------------------------------
# Cloud failure
# ---------------------------------------------------------------------------

class TestCloudFailure:
    async def test_returns_false_when_cloud_raises(self, hass, mock_api):
        device = _FakeDevice(lan_ip=None)
        mock_api.set_dps.side_effect = OSError("API down")

        result = await async_send_command(hass, mock_api, device, {"1": True})

        assert result is False

    async def test_returns_false_when_both_local_and_cloud_fail(self, hass, mock_api):
        device = _FakeDevice(lan_ip="192.168.1.50", local_key="abc", version="3.3")
        tinytuya_mock, _ = _make_tinytuya_mock(success=False)
        mock_api.set_dps.side_effect = OSError("API down")

        with patch.dict("sys.modules", {"tinytuya": tinytuya_mock}):
            result = await async_send_command(hass, mock_api, device, {"1": True})

        assert result is False
