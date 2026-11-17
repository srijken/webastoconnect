"""Get diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_LATITUDE, CONF_LONGITUDE, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import WebastoConfigEntry
from .api import WebastoConnectUpdateCoordinator

TO_REDACT = {
    CONF_PASSWORD,
    CONF_EMAIL,
    "title",
    "unique_id",
    CONF_LATITUDE,
    CONF_LONGITUDE,
    "lat",
    "lon",
    "acc_email",
    "stripe_key",
    "check_id",
}

# Settings options whose `value` holds personal data, matched on the option `key`.
SETTINGS_KEYS_TO_REDACT = {"SMS_users"}

REDACTED = "**REDACTED**"


def _redact_settings_values(settings: Any) -> Any:
    """Redact settings option values that hold personal data, such as phone numbers."""
    if not isinstance(settings, dict):
        return settings
    groups = settings.get("settings_tab")
    if not isinstance(groups, list):
        return settings

    redacted_groups = []
    for group in groups:
        if isinstance(group, dict) and isinstance(group.get("options"), list):
            group = {
                **group,
                "options": [
                    {**option, "value": REDACTED}
                    if isinstance(option, dict)
                    and option.get("key") in SETTINGS_KEYS_TO_REDACT
                    else option
                    for option in group["options"]
                ],
            }
        redacted_groups.append(group)
    return {**settings, "settings_tab": redacted_groups}


def _redact_device_id(payload: Any) -> Any:
    """Redact the top-level device id in a device data payload."""
    if isinstance(payload, dict) and "id" in payload:
        return {**payload, "id": REDACTED}
    return payload


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WebastoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data_dict = {
        "entry": entry.as_dict(),
        "devices": {},
    }

    api: WebastoConnectUpdateCoordinator = entry.runtime_data.coordinator

    for index, device in enumerate(api.cloud.devices.values(), start=1):
        # Device ids are printed on the device label and used for association,
        # so they are replaced by a sequence number.
        name = getattr(device, "name", None)
        if name is not None and str(name) == str(getattr(device, "device_id", None)):
            # Unnamed devices are named after their device id.
            name = REDACTED
        data_dict["devices"][f"device_{index}"] = {
            "device_id": REDACTED,
            "name": name,
            # Keep raw API payloads for bug reports while avoiding a full private object dump.
            "api_payload": {
                "last_data": _redact_device_id(getattr(device, "last_data", None)),
                "settings": _redact_settings_values(getattr(device, "settings", None)),
                "dev_data": _redact_device_id(getattr(device, "dev_data", None)),
            },
            "state": {
                "temperature": getattr(device, "temperature", None),
                "voltage": getattr(device, "voltage", None),
                "is_ventilation": getattr(device, "is_ventilation", None),
                "output_main": getattr(device, "output_main", None),
                "output_aux1": getattr(device, "output_aux1", None),
                "output_aux2": getattr(device, "output_aux2", None),
                "temperature_unit": getattr(device, "temperature_unit", None),
            },
        }

    return async_redact_data(data_dict, TO_REDACT)
