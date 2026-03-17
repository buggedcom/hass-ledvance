"""DataUpdateCoordinator for Ledvance/Tuya devices."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DeviceInfo, TuyaAPI
from .const import DEFAULT_SCAN_INTERVAL, LAN_SCAN_INTERVAL
from .exceptions import CannotConnect, InvalidAuthentication, TooManyRequests
from .schema_parser import detect_device_type, get_dps_map

_LOGGER = logging.getLogger(__name__)


@dataclass
class CoordinatorDeviceData:
    """All data needed to manage a single device."""

    device_id: str
    gateway_id: str
    name: str
    product_id: str
    device_type: str          # 'light' | 'switch' | 'unknown'
    schema: list
    dps_map: dict[str, str]   # code → dps_number_string
    dps: dict[str, object]    # dps_number_string → value
    local_key: str
    mac: str
    is_online: bool
    ip: str | None = None
    version: str | None = None
    room_name: str = ""  # Tuya room name; empty if unassigned


# Type alias for the coordinator data dict
DevicesData = dict[str, CoordinatorDeviceData]  # keyed by device_id


class LedvanceTuyaCoordinator(DataUpdateCoordinator[DevicesData]):
    """Polls device states — LAN-first with cloud fallback — and scans for local IPs."""

    def __init__(self, hass: HomeAssistant, api: TuyaAPI) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Ledvance/Tuya",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self._lan_cache: dict[str, dict] = {}   # device_id → {ip, version}
        self._last_lan_scan: float = 0.0

    # ------------------------------------------------------------------

    async def _async_update_data(self) -> DevicesData:
        """Fetch device states — LAN-first with cloud fallback — and keep LAN cache fresh."""
        # Refresh LAN scan when due (always on first call when cache is empty,
        # then every LAN_SCAN_INTERVAL seconds).  Running it BEFORE the device
        # fetch means local DPS polling is available from the very first update.
        now = time.monotonic()
        if not self._lan_cache or now - self._last_lan_scan >= LAN_SCAN_INTERVAL:
            try:
                scan_result = await self.hass.async_add_executor_job(self._scan_lan)
                self._lan_cache = scan_result
                self._last_lan_scan = now
                _LOGGER.debug("LAN scan found %d devices", len(scan_result))
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("LAN scan failed (non-fatal): %s", exc)

        try:
            devices_data = await self.hass.async_add_executor_job(
                self._fetch_all_devices
            )
        except InvalidAuthentication as exc:
            raise UpdateFailed(f"Authentication failed: {exc}") from exc
        except TooManyRequests as exc:
            raise UpdateFailed(f"Tuya API rate limited — will retry next poll: {exc}") from exc
        except CannotConnect as exc:
            raise UpdateFailed(f"Cannot connect to Tuya cloud: {exc}") from exc

        # Stamp the LAN ip/version onto each device entry for other consumers
        for dev_id, dev_data in devices_data.items():
            if dev_id in self._lan_cache:
                dev_data.ip = self._lan_cache[dev_id].get("ip")
                dev_data.version = self._lan_cache[dev_id].get("version")

        return devices_data

    def _fetch_all_devices(self) -> DevicesData:
        """Synchronous: fetch all devices from all groups."""
        # Re-login if needed (API auto-re-logins on session expiry,
        # but explicit login on first run or after auth error)
        if self.api.sid is None:
            self.api.login()

        result: DevicesData = {}
        try:
            groups = self.api.groups()
        except Exception as exc:
            raise CannotConnect(f"Failed to fetch groups: {exc}") from exc

        for group in groups:
            group_id = str(group.get("groupId") or group.get("locationId") or "")
            if not group_id:
                continue
            try:
                devices: list[DeviceInfo] = self.api.devices(group_id)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Failed to fetch devices for group %s: %s", group_id, exc)
                continue

            for dev_info in devices:
                # Get fresh DPS — try LAN first, fall back to cloud
                lan = self._lan_cache.get(dev_info.dev_id, {})
                dps = self._poll_dps_local(
                    dev_info.dev_id,
                    lan.get("ip"),
                    dev_info.local_key,
                    lan.get("version"),
                )
                if dps is not None:
                    _LOGGER.debug("DPS for '%s' fetched via LAN", dev_info.name)
                else:
                    try:
                        dps = self.api.get_dps(dev_info.dev_id)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.warning("Failed to fetch DPS for %s: %s", dev_info.dev_id, exc)
                        dps = dev_info.dps  # last resort: keep previous values

                device_type = detect_device_type(dev_info.schema, dev_info.product_id)
                dps_map = get_dps_map(dev_info.schema)

                result[dev_info.dev_id] = CoordinatorDeviceData(
                    device_id=dev_info.dev_id,
                    gateway_id=dev_info.gateway_id,
                    name=dev_info.name,
                    product_id=dev_info.product_id,
                    device_type=device_type,
                    schema=dev_info.schema,
                    dps_map=dps_map,
                    dps=dps,
                    local_key=dev_info.local_key,
                    mac=dev_info.mac,
                    is_online=dev_info.is_online,
                    room_name=dev_info.room_name,
                )

        _LOGGER.debug("Fetched %d devices from cloud", len(result))
        return result

    @staticmethod
    def _poll_dps_local(
        device_id: str,
        ip: str | None,
        local_key: str,
        version: str | None,
    ) -> dict | None:
        """Try to read DPS state from a device over LAN via tinytuya.

        Returns a normalised {dps_number_str: value} dict on success,
        or None if the device cannot be reached or tinytuya is unavailable.
        """
        if not (ip and local_key and version):
            return None
        try:
            import tinytuya  # noqa: PLC0415
        except ImportError:
            return None
        try:
            d = tinytuya.OutletDevice(
                dev_id=device_id,
                address=ip,
                local_key=local_key,
                version=float(version),
            )
            d.set_socketTimeout(3)
            status = d.status()
            if isinstance(status, dict) and "Error" not in status:
                raw = status.get("dps", {})
                return {str(k): v for k, v in raw.items()}
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Local DPS poll failed for %s (%s): %s", device_id, ip, exc)
        return None

    def _scan_lan(self) -> dict[str, dict]:
        """Synchronous: scan LAN for Tuya devices. Returns {device_id: {ip, version}}."""
        try:
            import tinytuya  # noqa: PLC0415
        except ImportError:
            _LOGGER.debug("tinytuya not available — skipping LAN scan")
            return {}

        found: dict[str, dict] = {}
        try:
            scan_result = tinytuya.deviceScan(maxretry=3, verbose=False, poll=False)
            for dev_id, dev in scan_result.items():
                found[dev_id] = {
                    "ip": dev.get("ip"),
                    "version": dev.get("ver") or dev.get("version"),
                }
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("tinytuya scan error: %s", exc)
        return found
