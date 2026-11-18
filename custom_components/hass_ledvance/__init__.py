"""Ledvance / Tuya Home Assistant integration."""

from __future__ import annotations

import difflib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .api import TuyaAPI
from .const import CONF_REGION, DOMAIN, PLATFORMS
from .coordinator import LedvanceTuyaCoordinator
from .exceptions import CannotConnect, InvalidAuthentication, InvalidUserSession, TooManyRequests

_LOGGER = logging.getLogger(__name__)

# Internal keys used to cache Tuya tokens across restarts
_CONF_SID = "_sid"
_CONF_REFRESH_TOKEN = "_refresh_token"
_CONF_AUTO_AREAS = "_auto_area_assignments"

type LedvanceTuyaConfigEntry = ConfigEntry[LedvanceTuyaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LedvanceTuyaConfigEntry) -> bool:
    """Set up Ledvance/Tuya from a config entry."""

    def _persist_tokens(new_sid: str, new_refresh: str) -> None:
        """Persist freshly obtained tokens into the config entry (thread-safe)."""
        hass.loop.call_soon_threadsafe(
            lambda: hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, _CONF_SID: new_sid, _CONF_REFRESH_TOKEN: new_refresh},
            )
        )
        _LOGGER.debug("Persisted new SID and refresh token")

    api = TuyaAPI.for_region(
        entry.data[CONF_REGION],
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        on_tokens_updated=_persist_tokens,
    )

    # Restore cached tokens — allows silent refresh without a full login
    cached_sid = entry.data.get(_CONF_SID)
    cached_refresh = entry.data.get(_CONF_REFRESH_TOKEN, "")
    if cached_sid:
        api.sid = cached_sid
        api.refresh_token = cached_refresh
        _LOGGER.debug(
            "Restored cached SID%s, skipping login", " and refresh token" if cached_refresh else ""
        )
    else:
        # No cached SID — must authenticate now (will send push notification)
        try:
            await hass.async_add_executor_job(api.login)
        except (InvalidAuthentication, InvalidUserSession) as exc:
            raise ConfigEntryAuthFailed("Invalid credentials") from exc
        except (CannotConnect, TooManyRequests) as exc:
            raise ConfigEntryNotReady("Cannot connect to Tuya cloud") from exc

    coordinator = LedvanceTuyaCoordinator(hass, api, entry)

    # First refresh — raises ConfigEntryNotReady on failure
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def assign_areas() -> None:
        _async_assign_areas(hass, coordinator, entry)

    assign_areas()
    entry.async_on_unload(coordinator.async_add_listener(assign_areas))
    _async_report_legacy_key_sensors(hass, entry)
    coordinator.async_start_lan_monitor()
    return True


def _find_best_area(room_name: str, area_by_name: dict[str, str]) -> tuple[str | None, str, float]:
    """Return (area_id, matched_name, score) for the closest HA area to a Tuya room name.

    Strategy (highest confidence first):
      1. Exact case-insensitive match         → score 1.0
      2. One name is a substring of the other → score 0.8
         (e.g. Tuya "Office" ↔ HA "Office Main")
      3. difflib fuzzy similarity ≥ 0.5       → score = ratio
    Returns (None, "", 0.0) when no match is good enough.
    """
    lower = room_name.lower()
    candidates = list(area_by_name.keys())

    # 1. Exact match
    if lower in area_by_name:
        return area_by_name[lower], lower, 1.0

    # 2. Substring containment — pick the candidate with the highest similarity
    #    among those that fully contain or are fully contained by the room name.
    subset = [c for c in candidates if lower in c or c in lower]
    if subset:
        best = max(subset, key=lambda c: difflib.SequenceMatcher(None, lower, c).ratio())
        return area_by_name[best], best, 0.8

    # 3. Fuzzy similarity across all candidates
    scored = sorted(
        ((difflib.SequenceMatcher(None, lower, c).ratio(), c) for c in candidates),
        reverse=True,
    )
    if scored and scored[0][0] >= 0.5:
        score, best = scored[0]
        return area_by_name[best], best, round(score, 2)

    return None, "", 0.0


@callback
def _async_assign_areas(
    hass: HomeAssistant,
    coordinator: LedvanceTuyaCoordinator,
    entry: LedvanceTuyaConfigEntry,
) -> None:
    """Match Tuya room names to existing HA areas and assign devices.

    Uses a tiered matching strategy: exact → substring → fuzzy (≥ 0.5 similarity).
    Devices with no room name, or with no plausible area match, are left unchanged.
    """
    area_reg = ar.async_get(hass)
    device_reg = dr.async_get(hass)

    # Build a lowercase name → area_id lookup from the current HA area registry
    area_by_name: dict[str, str] = {area.name.lower(): area.id for area in area_reg.areas.values()}

    saved_assignments = entry.data.get(_CONF_AUTO_AREAS, {})
    assignments = dict(saved_assignments)
    get_device_by_identifier = getattr(device_reg, "async_get_device_by_identifier", None)
    for dev_id, dev_data in coordinator.data.items():
        if dev_id in assignments and assignments[dev_id] is None:
            continue
        identifiers = (DOMAIN, dev_id)
        device_entry = (
            get_device_by_identifier(identifiers, entry.entry_id)
            if get_device_by_identifier is not None
            else device_reg.async_get_device(identifiers={identifiers})
        )
        if device_entry is None:
            continue

        # An explicit None keeps a manual assignment or clear across refreshes and reloads.
        if device_entry.area_id != assignments.get(dev_id):
            assignments[dev_id] = None
            continue
        if not dev_data.room_name:
            continue

        area_id, matched_name, score = _find_best_area(dev_data.room_name, area_by_name)

        if area_id is None:
            _LOGGER.debug(
                "No area match for Tuya room '%s' (device '%s') — skipping",
                dev_data.room_name,
                dev_data.name,
            )
            continue

        if device_entry.area_id != area_id:
            device_reg.async_update_device(device_entry.id, area_id=area_id)
            _LOGGER.debug(
                "Assigned device '%s' → area '%s' (matched Tuya room '%s', score %.2f)",
                dev_data.name,
                matched_name,
                dev_data.room_name,
                score,
            )
        assignments[dev_id] = area_id

    if assignments != saved_assignments:
        hass.config_entries.async_update_entry(entry, data={**entry.data, _CONF_AUTO_AREAS: assignments})


@callback
def _async_report_legacy_key_sensors(hass: HomeAssistant, entry: LedvanceTuyaConfigEntry) -> None:
    """Point affected upgrades to Recorder cleanup without deleting history."""
    legacy_entities = sorted(
        entity.entity_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if entity.platform == DOMAIN
        and entity.domain == "sensor"
        and entity.unique_id.endswith("_local_key")
        and entity.disabled_by is not er.RegistryEntryDisabler.INTEGRATION
    )
    issue_id = f"legacy_local_key_{entry.entry_id}"
    if legacy_entities:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="legacy_local_key",
            translation_placeholders={"entity_ids": ", ".join(legacy_entities)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


async def async_unload_entry(hass: HomeAssistant, entry: LedvanceTuyaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
