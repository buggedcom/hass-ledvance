"""Ledvance / Tuya Home Assistant integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

from .api import TuyaAPI
from .const import CONF_REGION, DOMAIN, PLATFORMS, REGIONS
from .coordinator import LedvanceTuyaCoordinator
from .exceptions import CannotConnect, InvalidAuthentication, TooManyRequests

_LOGGER = logging.getLogger(__name__)

# Internal keys used to cache Tuya tokens across restarts
_CONF_SID = "_sid"
_CONF_REFRESH_TOKEN = "_refresh_token"

type LedvanceTuyaConfigEntry = ConfigEntry[LedvanceTuyaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LedvanceTuyaConfigEntry) -> bool:
    """Set up Ledvance/Tuya from a config entry."""
    region_cfg = REGIONS[entry.data[CONF_REGION]]

    def _persist_tokens(new_sid: str, new_refresh: str) -> None:
        """Persist freshly obtained tokens into the config entry (thread-safe)."""
        hass.loop.call_soon_threadsafe(
            lambda: hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, _CONF_SID: new_sid, _CONF_REFRESH_TOKEN: new_refresh},
            )
        )
        _LOGGER.debug("Persisted new SID and refresh token")

    api = TuyaAPI(
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        endpoint=region_cfg["endpoint"],
        country_code=region_cfg["country_code"],
        on_tokens_updated=_persist_tokens,
    )

    # Restore cached tokens — allows silent refresh without a full login
    cached_sid = entry.data.get(_CONF_SID)
    cached_refresh = entry.data.get(_CONF_REFRESH_TOKEN, "")
    if cached_sid:
        api.sid = cached_sid
        api.refresh_token = cached_refresh
        _LOGGER.debug("Restored cached SID%s, skipping login",
                      " and refresh token" if cached_refresh else "")
    else:
        # No cached SID — must authenticate now (will send push notification)
        try:
            await hass.async_add_executor_job(api.login)
        except InvalidAuthentication as exc:
            raise ConfigEntryAuthFailed("Invalid credentials") from exc
        except (CannotConnect, TooManyRequests) as exc:
            raise ConfigEntryNotReady("Cannot connect to Tuya cloud") from exc

    coordinator = LedvanceTuyaCoordinator(hass, api)

    # First refresh — raises ConfigEntryNotReady on failure
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Best-effort: assign HA areas that match Tuya room names.
    # Must run after platform setup so device-registry entries exist.
    await _async_assign_areas(hass, coordinator)

    return True


async def _async_assign_areas(
    hass: HomeAssistant,
    coordinator: LedvanceTuyaCoordinator,
) -> None:
    """Match Tuya room names to existing HA areas and assign devices.

    Only assigns devices when an HA area already exists whose name matches
    the Tuya room name (case-insensitive). Devices with no room name, or
    whose room name has no match, are left unchanged.
    """
    area_reg = ar.async_get(hass)
    device_reg = dr.async_get(hass)

    # Build a lowercase name → area_id lookup from the current HA area registry
    area_by_name: dict[str, str] = {
        area.name.lower(): area.id
        for area in area_reg.areas.values()
    }

    for dev_id, dev_data in coordinator.data.items():
        if not dev_data.room_name:
            continue

        area_id = area_by_name.get(dev_data.room_name.lower())
        if area_id is None:
            _LOGGER.debug(
                "Tuya room '%s' for device '%s' has no matching HA area — skipping",
                dev_data.room_name,
                dev_data.name,
            )
            continue

        device_entry = device_reg.async_get_device(identifiers={(DOMAIN, dev_id)})
        if device_entry is None:
            continue

        # Only update if the area isn't already set to the same value
        if device_entry.area_id != area_id:
            device_reg.async_update_device(device_entry.id, area_id=area_id)
            _LOGGER.debug(
                "Assigned device '%s' → area '%s'",
                dev_data.name,
                dev_data.room_name,
            )


async def async_unload_entry(hass: HomeAssistant, entry: LedvanceTuyaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
