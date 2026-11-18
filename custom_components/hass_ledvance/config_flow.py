"""Config flow for Ledvance/Tuya integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .api import TuyaAPI
from .const import CONF_REGION, DEFAULT_REGION, DOMAIN, REGIONS, region_app
from .exceptions import CannotConnect, InvalidAuthentication, InvalidUserSession, TooManyRequests

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): vol.In(list(REGIONS)),
    }
)


async def _validate_credentials(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Raise on auth or connectivity failure."""
    api = TuyaAPI.for_region(data[CONF_REGION], data[CONF_EMAIL], data[CONF_PASSWORD])
    await hass.async_add_executor_job(api.login)


class LedvanceTuyaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Ledvance/Tuya config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _validate_credentials(self.hass, user_input)
            except (InvalidAuthentication, InvalidUserSession):
                _LOGGER.warning(
                    "Authentication failed (region=%s, app=%s)",
                    user_input[CONF_REGION],
                    region_app(user_input[CONF_REGION]),
                )
                errors["base"] = "invalid_auth"
            except TooManyRequests:
                errors["base"] = "rate_limited"
            except CannotConnect as exc:
                _LOGGER.warning(
                    "Cannot connect to Tuya cloud (region=%s): %s",
                    user_input[CONF_REGION],
                    exc,
                )
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                # Prevent duplicate entries for same account
                await self.async_set_unique_id(
                    f"{user_input[CONF_EMAIL]}_{user_input[CONF_REGION]}"
                )
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"{user_input[CONF_EMAIL]} ({user_input[CONF_REGION]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> config_entries.FlowResult:
        """Start reauthentication for the existing config entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Request an updated password, keeping the account and region unchanged."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            updated = {
                CONF_EMAIL: entry.data[CONF_EMAIL],
                CONF_REGION: entry.data[CONF_REGION],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                await _validate_credentials(self.hass, updated)
            except (InvalidAuthentication, InvalidUserSession):
                errors["base"] = "invalid_auth"
            except TooManyRequests:
                errors["base"] = "rate_limited"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during reauthentication")
                errors["base"] = "unknown"
            else:
                # Cached tokens belong to the previous credentials.
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={**entry.data, **updated, "_sid": None, "_refresh_token": ""},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
