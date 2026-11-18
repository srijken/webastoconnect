"""Config flow for setting up the integration."""

import logging
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from pywebasto import WebastoConnect
from pywebasto.exceptions import InvalidRequestException, UnauthorizedException

try:
    from pywebasto.exceptions import (
        ForbiddenException,
        InvalidResponseException,
        TooManyRequestsException,
    )
except ImportError:
    ForbiddenException = InvalidRequestException
    InvalidResponseException = InvalidRequestException
    TooManyRequestsException = InvalidRequestException

from .api import _credential_callbacks
from .const import DOMAIN

LOGGER = logging.getLogger(__name__)

CONF_SCHEME = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


PASSWORD_SCHEME = vol.Schema({vol.Required(CONF_PASSWORD): str})


def _normalize_email(email: str) -> str:
    """Return the email address in the form used as config entry unique ID."""
    return email.strip().lower()


async def _async_validate_credentials(
    hass: HomeAssistant,
    email: str,
    password: str,
    entry: config_entries.ConfigEntry | None = None,
) -> dict[str, str]:
    """Log in with the given credentials and return form errors, if any.

    With an existing entry, the stored app client credentials are reused so
    validating does not register a new app client.
    """
    integration = await async_get_integration(hass, DOMAIN)
    kwargs: dict[str, Any] = {}
    if entry is not None:
        credential_load, credential_save = _credential_callbacks(hass, entry)
        kwargs = {
            "credential_load": credential_load,
            "credential_save": credential_save,
        }

    webasto = WebastoConnect(
        username=email,
        password=password,
        client_info=f"HomeAssistant-Webasto {integration.version} {int(datetime.now().timestamp())}",
        **kwargs,
    )
    errors: dict[str, str] = {}
    try:
        await webasto.connect()
        LOGGER.debug("Authorization OK")
    except UnauthorizedException:
        LOGGER.debug("Authorization ERROR")
        errors["base"] = "invalid_auth"
    except (
        InvalidRequestException,
        ForbiddenException,
        InvalidResponseException,
    ):
        LOGGER.debug("Connection validation failed")
        errors["base"] = "cannot_connect"
    except TooManyRequestsException:
        LOGGER.debug("Connection validation rate limited")
        errors["base"] = "ratelimit"
    finally:
        await webasto.close()
    return errors


class WebastoConnectConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Webasto Connect.

    Credentials live in the entry data only. They are changed with the
    reauth and reconfigure flows, not with an options flow, so the password is
    never stored in options or pre-filled in a form.
    """

    VERSION = 1
    MINOR_VERSION = 3

    async def async_step_user(self, user_input: Any | None = None):
        """Handle the initial config flow step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = str(user_input[CONF_EMAIL]).strip()
            password = user_input[CONF_PASSWORD]
            # New entries use the lowercase email as unique ID. Entries created
            # before this used the email as typed, so compare those too.
            await self.async_set_unique_id(_normalize_email(email))
            self._abort_if_unique_id_configured()
            if any(
                _normalize_email(str(entry.data.get(CONF_EMAIL, ""))) == self.unique_id
                for entry in self._async_current_entries(include_ignore=False)
            ):
                return self.async_abort(reason="already_configured")

            errors = await _async_validate_credentials(self.hass, email, password)
            if not errors:
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: password},
                    description=f"Webasto ThermoConnect - {email}",
                )

        LOGGER.debug("Showing configuration form")
        return self.async_show_form(
            step_id="user", data_schema=CONF_SCHEME, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle reauthorization if credentials are no longer valid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for the account password again after an authentication failure."""
        return await self._async_step_password(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Change the account password."""
        return await self._async_step_password(
            "reconfigure", self._get_reconfigure_entry(), user_input
        )

    async def _async_step_password(
        self,
        step_id: str,
        entry: config_entries.ConfigEntry,
        user_input: dict[str, Any] | None,
    ) -> config_entries.ConfigFlowResult:
        """Validate a new password for an entry and store it in the entry data."""
        email = entry.data[CONF_EMAIL]
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await _async_validate_credentials(
                self.hass, email, user_input[CONF_PASSWORD], entry
            )
            if not errors:
                # Schedules a reload, also when setup had failed on the old password.
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                    reason=(
                        "reconfigure_successful"
                        if step_id == "reconfigure"
                        else "reauth_successful"
                    ),
                )

        return self.async_show_form(
            step_id=step_id,
            data_schema=PASSWORD_SCHEME,
            description_placeholders={CONF_EMAIL: email},
            errors=errors,
        )
