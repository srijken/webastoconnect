"""Binary sensors for Webasto Connect."""

import logging
from typing import cast

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from . import WebastoConfigEntry
from .api import WebastoConnectUpdateCoordinator
from .base import WebastoBaseEntity, WebastoConnectBinarySensorEntityDescription

LOGGER = logging.getLogger(__name__)
# Read-only entities updated by the coordinator.
PARALLEL_UPDATES = 0

BINARY_SENSORS = [
    WebastoConnectBinarySensorEntityDescription(
        key="is_connected",
        translation_key="is_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda webasto: cast(bool | None, webasto.is_connected),
    ),
    WebastoConnectBinarySensorEntityDescription(
        key="allow_location",
        translation_key="allow_location",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda webasto: cast(bool, webasto.allow_location),
        entity_registry_enabled_default=False,
    ),
    WebastoConnectBinarySensorEntityDescription(
        key="low_voltage_warning",
        translation_key="low_voltage_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda webasto: getattr(webasto, "low_voltage_warning", None),
    ),
    WebastoConnectBinarySensorEntityDescription(
        key="geofence_enabled",
        translation_key="geofence_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda webasto: getattr(webasto, "geofence_enabled", None),
        entity_registry_enabled_default=False,
    ),
    WebastoConnectBinarySensorEntityDescription(
        key="signal_lost_shutdown_enabled",
        translation_key="signal_lost_shutdown_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda webasto: getattr(webasto, "signal_lost_shutdown_enabled", None),
        entity_registry_enabled_default=False,
    ),
]


async def async_setup_entry(hass, entry: WebastoConfigEntry, async_add_devices):
    """Set up binary sensors."""
    binarysensors = []

    coordinator = entry.runtime_data.coordinator

    for id, device in coordinator.cloud.devices.items():
        LOGGER.debug("Setting up binary_sensors for device: %s", device.name)
        for b_s in BINARY_SENSORS:
            entity = WebastoConnectBinarySensor(id, b_s, coordinator)
            LOGGER.debug("Adding binary_sensor '%s'", b_s.key)
            binarysensors.append(entity)

    async_add_devices(binarysensors)


class WebastoConnectBinarySensor(
    WebastoBaseEntity,
    BinarySensorEntity,
):
    """Representation of a Webasto Connect Binary Sensor."""

    def __init__(
        self,
        device_id: int,
        description: WebastoConnectBinarySensorEntityDescription,
        coordinator: WebastoConnectUpdateCoordinator,
    ) -> None:
        """Initialize a Webasto Connect Binary Sensor."""
        super().__init__(device_id, coordinator, description)

        self._attr_is_on = self.entity_description.value_fn(  # type: ignore
            self._cloud.devices[self._device_id]
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        new_is_on = self.entity_description.value_fn(  # type: ignore
            self._cloud.devices[self._device_id]
        )
        changed = new_is_on != self._attr_is_on
        self._attr_is_on = new_is_on
        self._async_write_if_changed(changed)
