"""Add Webasto ThermoConnect support to Home Assistant."""

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from pywebasto.enums import Request
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

from .api import WebastoConnectUpdateCoordinator, async_remove_credentials
from .base import webasto_device_name
from .card_frontend import async_register_card, async_remove_legacy_card
from .const import DOMAIN, PLATFORMS, STARTUP
from .migration import (
    KEY_UNIQUE_ID_MINOR_VERSION,
    async_migrate_to_key_unique_ids,
)
from .services import async_setup_services
from .subscription import SubscriptionIssueTracker, async_delete_subscription_issues

LOGGER = logging.getLogger(__name__)
PENDING_APPROVAL_ISSUE_ID = "pending_approval"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class WebastoRuntimeData:
    """Runtime data for the Webasto config entry."""

    coordinator: WebastoConnectUpdateCoordinator


type WebastoConfigEntry = ConfigEntry[WebastoRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up actions and the bundled card; they stay registered while HA runs."""
    async_setup_services(hass)
    await async_register_card(hass)
    await async_remove_legacy_card(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries.

    1.2: credentials move from the entry options (written by the removed
    options flow) to the entry data, and are removed from the options.
    1.3: entity unique IDs become key-based. That step needs the output names
    from the cloud, so it runs in setup (`_async_migrate_to_key_unique_ids`).
    """
    if entry.version > 1:
        return False

    if entry.minor_version < 2:
        data = dict(entry.data)
        options = dict(entry.options)
        for key in (CONF_EMAIL, CONF_PASSWORD):
            if key in options:
                data[key] = options.pop(key)
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, minor_version=2
        )
        LOGGER.debug("Migrated config entry %s to version 1.2", entry.entry_id)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: WebastoConfigEntry) -> bool:
    """Set up cloud API connector from a config entry."""
    coordinator = await _async_setup(hass, entry)
    # No update listener: the reauth and reconfigure flows schedule the reload.
    entry.runtime_data = WebastoRuntimeData(coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_migrate_to_key_unique_ids(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: WebastoConnectUpdateCoordinator,
) -> None:
    """Move entities to key-based unique IDs once (config entry 1.2 -> 1.3).

    Runs after connecting, because mapping an old name-based unique ID to its
    entity needs the output names, and before the platforms add entities.
    Without devices from the cloud nothing can be mapped, so it is retried on
    the next setup.
    """
    if entry.version != 1 or entry.minor_version >= KEY_UNIQUE_ID_MINOR_VERSION:
        return
    if not coordinator.cloud.devices:
        LOGGER.debug("No devices reported yet; unique ID migration postponed")
        return

    await async_migrate_to_key_unique_ids(
        hass,
        entry.entry_id,
        {
            str(device_id): device
            for device_id, device in coordinator.cloud.devices.items()
        },
    )
    hass.config_entries.async_update_entry(
        entry, minor_version=KEY_UNIQUE_ID_MINOR_VERSION
    )
    LOGGER.debug("Migrated config entry %s to version 1.3", entry.entry_id)


async def _async_update_device_registry_name(
    hass: HomeAssistant,
    device_id: int,
    device_name: str,
) -> None:
    """Update the device registry name."""
    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_device({(DOMAIN, str(device_id))})
    if device_entry is None or device_entry.name_by_user is not None:
        return
    if device_entry.name == device_name:
        return

    device_registry.async_update_device(device_entry.id, name=device_name)


def _device_names_from_webapi_data(data: dict[str, Any]) -> dict[str, str]:
    """Return device names from webapi data."""
    names = {}
    if data.get("id") and data.get("alias"):
        names[str(data["id"])] = str(data["alias"])

    for device in data.get("account_info", {}).get("devices", []):
        if isinstance(device, dict):
            device_id = (
                device.get("id") or device.get("device_id") or device.get("dev_id")
            )
            device_name = device.get("name") or device.get("alias")
        else:
            device_id = device[0] if len(device) > 0 else None
            device_name = device[1] if len(device) > 1 else None
        if device_id and device_name:
            names[str(device_id)] = str(device_name)

    return names


async def _async_webapi_device_names(
    coordinator: WebastoConnectUpdateCoordinator,
) -> dict[str, str]:
    """Fetch device names from webapi when available."""
    if not coordinator.cloud.uses_webapi_session:
        return {}

    try:
        data = await coordinator.cloud._call(Request.GET_DATA_NOPOLL)  # noqa: SLF001
    except (
        InvalidRequestException,
        ForbiddenException,
        InvalidResponseException,
    ) as err:
        LOGGER.debug("Could not fetch Webasto webapi device names: %s", err)
        return {}

    if not isinstance(data, dict):
        return {}

    return _device_names_from_webapi_data(data)


async def _async_setup(
    hass: HomeAssistant, entry: WebastoConfigEntry
) -> WebastoConnectUpdateCoordinator:
    """Set up the integration using a config entry."""
    integration = await async_get_integration(hass, DOMAIN)
    LOGGER.info(
        STARTUP,
        integration.version,
    )
    coordinator = WebastoConnectUpdateCoordinator(hass, entry, integration.version)
    # Closes the cloud session on unload and on every failed setup path.
    entry.async_on_unload(coordinator.cloud.close)
    try:
        await coordinator.cloud.connect()
        LOGGER.debug(
            "Connected to Webasto API for %s",
            entry.data.get(CONF_EMAIL),
        )
    except UnauthorizedException:
        raise ConfigEntryAuthFailed("Invalid email or password specified") from None
    except InvalidRequestException as err:
        raise ConfigEntryError(f"Webasto API rejected setup request: {err}") from err
    except (InvalidResponseException, ForbiddenException) as err:
        raise ConfigEntryNotReady(
            f"Error connecting to the API - try again later: {err}"
        ) from err
    except TooManyRequestsException as err:
        # Temporary: let Home Assistant retry setup with backoff.
        raise ConfigEntryNotReady(f"Rate limited by the Webasto API: {err}") from err

    webapi_device_names = await _async_webapi_device_names(coordinator)
    coordinator.device_names = webapi_device_names

    pending_devices = [
        device
        for device in coordinator.cloud.devices.values()
        if getattr(device, "pending_approval", False)
    ]
    if pending_devices:
        devices = ", ".join(
            webasto_device_name(
                device,
                webapi_device_names.get(str(device.device_id)),
            )
            for device in pending_devices
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            PENDING_APPROVAL_ISSUE_ID,
            data={"entry_id": entry.entry_id},
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="pending_approval",
            translation_placeholders={"devices": devices},
        )
        raise ConfigEntryError(
            "Webasto device association is pending approval in the ThermoConnect app"
        )

    ir.async_delete_issue(hass, DOMAIN, PENDING_APPROVAL_ISSUE_ID)

    # Runs on the first data below and after every successful update.
    SubscriptionIssueTracker(hass, entry, coordinator).async_start()

    if coordinator.cloud.devices:
        # connect() already hydrated device state, avoid an immediate duplicate update call.
        coordinator.async_set_updated_data(None)
    else:
        await coordinator.async_config_entry_first_refresh()

    for id, device in coordinator.cloud.devices.items():
        device_name = webasto_device_name(device, webapi_device_names.get(str(id)))
        LOGGER.debug("Found device: %s", device_name)
        await _async_update_device_registry_name(hass, id, device_name)

    await _async_migrate_to_key_unique_ids(hass, entry, coordinator)

    return coordinator


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored app client credentials and the entry's issues."""
    async_delete_subscription_issues(hass, entry)
    await async_remove_credentials(hass, entry)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: WebastoConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removing a device that the Webasto account no longer reports.

    Without a loaded entry the current device list is unknown, so the device
    is kept.
    """
    runtime_data: WebastoRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return False

    current_ids = {
        str(device_id) for device_id in runtime_data.coordinator.cloud.devices
    }
    return not any(
        domain == DOMAIN and identifier in current_ids
        for domain, identifier in device_entry.identifiers
    )


async def async_unload_entry(hass: HomeAssistant, entry: WebastoConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        await entry.runtime_data.coordinator.cloud.close()
    return unload_ok
