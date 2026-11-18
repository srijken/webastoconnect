"""Base definitions."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import callback
from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from pywebasto import WebastoConnect, WebastoDevice

from .api import WebastoConnectUpdateCoordinator
from .const import DOMAIN
from .migration import key_unique_id


def webasto_device_name(device: WebastoDevice, fallback_name: str | None = None) -> str:
    """Return the configured device name."""
    for data in (getattr(device, "dev_data", None), getattr(device, "app_data", None)):
        if not isinstance(data, dict):
            continue
        if name := data.get("name") or data.get("alias"):
            return str(name)

    if fallback_name:
        return fallback_name

    return device.name


@dataclass(frozen=True)
class WebastoConnectBaseEntityDescriptionMixin:
    """Describes a basic Webasto entity."""

    value_fn: Callable[[WebastoDevice], bool | str | int | float]


@dataclass(frozen=True)
class WebastoConnectBinarySensorEntityDescription(
    BinarySensorEntityDescription, WebastoConnectBaseEntityDescriptionMixin
):
    """Describes a Webasto binary sensor."""


@dataclass(frozen=True)
class WebastoConnectSensorEntityDescription(SensorEntityDescription):
    """Describes a Webasto sensor."""

    value_fn: Callable[[Any], Any | None] | None = None
    unit_fn: Callable[[Any], Any] | None = None
    # Values for the placeholders in the translated entity name.
    placeholders_fn: Callable[[Any], Mapping[str, str]] | None = None


@dataclass(frozen=True)
class WebastoConnectSwitchEntityDescription(
    SwitchEntityDescription, WebastoConnectBaseEntityDescriptionMixin
):
    """Describes a Webasto switch."""

    # allow command_fn to accept (webasto, id, state) by using a variadic Callable
    command_fn: Callable[..., Any] | None = None
    type_fn: Callable[[WebastoConnect], None] | None = None
    # Name set in the Webasto app. It wins over the translated name; False
    # when the device has no such output.
    name_fn: Callable[[WebastoDevice], str | bool] | None = None


@dataclass(frozen=True)
class WebastoConnectNumberEntityDescription(
    NumberEntityDescription, WebastoConnectBaseEntityDescriptionMixin
):
    """Describes a Webasto number."""

    value_fn: Callable[[Any], Any]
    set_fn: Callable[[WebastoConnect, WebastoDevice, float], Any] | None = None
    unit_fn: Callable[[WebastoDevice], Any] | None = None


def _version_string(value: Any) -> str | None:
    """Return a version for the device registry, which requires strings.

    The web API reports sw_version as an int (e.g. 152); Home Assistant
    rejects non-string versions from 2026.12.
    """
    if value is None or value == "":
        return None
    return str(value)


class WebastoBaseEntity(CoordinatorEntity[DataUpdateCoordinator[None]]):
    """Base Webasto Connect Entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        device_id: int,
        coordinator: WebastoConnectUpdateCoordinator,
        description: (
            WebastoConnectBinarySensorEntityDescription
            | WebastoConnectSensorEntityDescription
            | WebastoConnectSwitchEntityDescription
            | WebastoConnectNumberEntityDescription
            | EntityDescription
        ),
    ) -> None:
        """Initialize a Webasto Connect Entity."""
        super().__init__(coordinator)

        self.entity_description = description
        self._config = coordinator.config_entry
        self._hass = coordinator.hass
        self._device_id = device_id
        self._cloud: WebastoConnect = coordinator.cloud

        # The name comes from translations (translation_key), except for
        # outputs named in the Webasto app; see _update_name.
        self._update_name()

        # Stable across output renames and heating/ventilation mode changes.
        self._attr_unique_id = key_unique_id(
            self._cloud.devices[self._device_id].device_id,
            self.entity_description.key,
        )

        settings = self._cloud.devices[self._device_id].settings or {}

        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(self._device_id))},
            "name": self._device_name,
            "model": "ThermoConnect",
            "manufacturer": "Webasto",
            "hw_version": _version_string(settings.get("hw_version")),
            "sw_version": _version_string(settings.get("sw_version")),
            "configuration_url": "https://my.webastoconnect.com",
        }

    def _update_name(self) -> bool:
        """Apply the name set in the Webasto app, if any.

        A non-empty name from the cloud is used as-is. Otherwise the entity
        has no ``_attr_name`` and Home Assistant uses the translated name.
        Return True when the name changed.
        """
        name_fn = getattr(self.entity_description, "name_fn", None)
        if name_fn is None:
            return False
        name = name_fn(self._cloud.devices[self._device_id])
        if isinstance(name, str) and name.strip():
            changed = getattr(self, "_attr_name", None) != name
            self._attr_name = name
            return changed
        if hasattr(self, "_attr_name"):
            del self._attr_name
            return True
        return False

    @property
    def _device(self) -> WebastoDevice:
        """Return the current device model for this entity."""
        return self._cloud.devices[self._device_id]

    @property
    def _device_name(self) -> str:
        """Return the configured device name."""
        coordinator = getattr(self, "coordinator", None)
        device_names = getattr(coordinator, "device_names", {})
        return webasto_device_name(self._device, device_names.get(str(self._device_id)))

    @property
    def _coordinator_ok(self) -> bool:
        """Return whether the last coordinator update succeeded."""
        coordinator = getattr(self, "coordinator", None)
        if coordinator is None:
            return True
        return bool(coordinator.last_update_success)

    @property
    def available(self) -> bool:
        """Unavailable when the last update from the cloud failed."""
        return self._coordinator_ok

    @callback
    def _async_write_if_changed(self, changed: bool) -> None:
        """Write state when the value or the availability changed."""
        available = self.available
        if changed or available != getattr(self, "_written_available", True):
            self._written_available = available
            self.async_write_ha_state()

    @property
    def _is_device_connected(self) -> bool:
        """Return False only when the device is explicitly disconnected."""
        return getattr(self._device, "is_connected", True) is not False
