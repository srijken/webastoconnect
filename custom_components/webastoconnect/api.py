"""API connector class."""

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
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

from .const import DOMAIN

SCAN_INTERVAL = timedelta(seconds=60)
LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


CREDENTIAL_STORAGE_VERSION = 1


def _credential_store_key(entry: ConfigEntry) -> str:
    """Return the storage key of the app credentials for a config entry."""
    return f"{DOMAIN}.credentials.{entry.entry_id}"


def _credential_store(hass: HomeAssistant, entry: ConfigEntry) -> Store[dict[str, str]]:
    """Return the Store holding the pywebasto app credentials of an entry.

    The client secret is a bearer credential for the heater, so the file is
    written atomically and readable by the owner only.
    """
    return Store(
        hass,
        CREDENTIAL_STORAGE_VERSION,
        _credential_store_key(entry),
        private=True,
        atomic_writes=True,
    )


def _legacy_credential_path(hass: HomeAssistant, entry: ConfigEntry) -> Path:
    """Return the path of the pre-Store app credential file of an entry."""
    return Path(hass.config.path(".storage", f"webasto_{entry.entry_id}.json"))


def _load_legacy_credentials(path: Path) -> dict[str, str] | None:
    """Load app credentials from the pre-Store credential file, if present."""
    if not path.exists():
        return None

    with path.open(encoding="utf-8") as credential_file:
        return json.load(credential_file)


def _remove_legacy_credentials(path: Path) -> None:
    """Delete the pre-Store app credential file, if present."""
    path.unlink(missing_ok=True)


async def _async_load_credentials(
    hass: HomeAssistant, entry: ConfigEntry, store: Store[dict[str, str]]
) -> dict[str, str] | None:
    """Load app credentials, moving a pre-Store credential file into the Store."""
    if (credentials := await store.async_load()) is not None:
        return credentials

    path = _legacy_credential_path(hass, entry)
    credentials = await hass.async_add_executor_job(_load_legacy_credentials, path)
    if credentials is None:
        return None

    # Save first: the old file is only deleted once the Store holds the data.
    await store.async_save(credentials)
    await hass.async_add_executor_job(_remove_legacy_credentials, path)
    LOGGER.debug("Moved Webasto app credentials of %s to storage", entry.entry_id)
    return credentials


async def async_remove_credentials(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored app credentials of an entry, also a pre-Store file."""
    await _credential_store(hass, entry).async_remove()
    await hass.async_add_executor_job(
        _remove_legacy_credentials, _legacy_credential_path(hass, entry)
    )


def _credential_callbacks(
    hass: HomeAssistant, entry: ConfigEntry
) -> tuple[
    Callable[[], Awaitable[dict[str, str] | None]], Callable[[Any], Awaitable[None]]
]:
    """Return pywebasto credential callbacks."""
    store = _credential_store(hass, entry)

    async def credential_load() -> dict[str, str] | None:
        return await _async_load_credentials(hass, entry, store)

    async def credential_save(credentials: Any) -> None:
        await store.async_save(
            {
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
            }
        )

    return credential_load, credential_save


class WebastoConnectUpdateCoordinator(DataUpdateCoordinator[None]):
    """webasto Connect data update coordinator."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, version: str = "unknown"
    ) -> None:
        """Initialize the connection."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

        credential_load, credential_save = _credential_callbacks(hass, entry)
        self.cloud: WebastoConnect = WebastoConnect(
            username=entry.data.get(CONF_EMAIL),
            password=entry.data.get(CONF_PASSWORD),
            credential_load=credential_load,
            credential_save=credential_save,
            client_info=f"HomeAssistant-Webasto {version} {int(datetime.now().timestamp())}",
        )
        self._cloud_operation_lock = asyncio.Lock()
        self.device_names: dict[str, str] = {}

    async def async_execute_cloud_call(
        self,
        cloud_call: Callable[..., Awaitable[_T]],
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """Serialize cloud operations to avoid device context races."""
        async with self._cloud_operation_lock:
            return await cloud_call(*args, **kwargs)

    async def _async_update_data(self) -> datetime | None:
        """Handle data update request from the coordinator."""
        LOGGER.debug("Data update called")
        try:
            await self.async_execute_cloud_call(self.cloud.update)
        except UnauthorizedException as err:
            raise ConfigEntryAuthFailed("Authentication with Webasto failed") from err
        except InvalidRequestException as err:
            raise UpdateFailed(f"Webasto API request failed: {err}") from err
        except (ForbiddenException, InvalidResponseException) as err:
            raise UpdateFailed(
                f"Webasto API temporary failure: {err}",
                retry_after=300,
            ) from err
        except TooManyRequestsException as err:
            raise UpdateFailed(
                f"Webasto API rate limited: {err}",
                retry_after=300,
            ) from err
        except Exception as err:
            LOGGER.exception(
                "Unexpected error during Webasto update (%s)",
                type(err).__name__,
            )
            raise UpdateFailed(
                f"Unexpected update failure ({type(err).__name__}): {err}",
                retry_after=300,
            ) from err
