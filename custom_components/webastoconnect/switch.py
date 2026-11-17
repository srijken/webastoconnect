"""Switches for Webasto Connect."""

import logging
from typing import Any, cast

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from . import WebastoConfigEntry
from .api import WebastoConnectUpdateCoordinator
from .base import WebastoBaseEntity, WebastoConnectSwitchEntityDescription

LOGGER = logging.getLogger(__name__)


def _supports_ventilation_mode(device: Any) -> bool:
    """Return False only when the device reports ventilation mode as unavailable."""
    features = getattr(device, "output_main_features", None)
    if not isinstance(features, dict):
        return True
    return features.get("ventilation_mode_available") is not False


SWITCHES = [
    WebastoConnectSwitchEntityDescription(
        key="main_output",
        name="Output",
        entity_category=None,
        value_fn=lambda webasto: cast(bool, webasto.output_main),
        command_fn=lambda webasto, id, state: webasto.set_output_main(id, state),
        name_fn=lambda webasto: webasto.output_main_name,
        entity_registry_enabled_default=True,
    ),
    WebastoConnectSwitchEntityDescription(
        key="ventilation_mode",
        name="Ventilation Mode",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda webasto: cast(bool, webasto.is_ventilation),
        command_fn=lambda webasto, id, state: webasto.ventilation_mode(id, state),
        entity_registry_enabled_default=False,
    ),
    WebastoConnectSwitchEntityDescription(
        key="aux1_output",
        name="AUX1",
        entity_category=None,
        value_fn=lambda webasto: cast(bool, webasto.output_aux1),
        command_fn=lambda webasto, id, state: webasto.set_output_aux1(id, state),
        name_fn=lambda webasto: webasto.output_aux1_name,
        entity_registry_enabled_default=True,
    ),
    WebastoConnectSwitchEntityDescription(
        key="aux2_output",
        name="AUX2",
        entity_category=None,
        value_fn=lambda webasto: cast(bool, webasto.output_aux2),
        command_fn=lambda webasto, id, state: webasto.set_output_aux2(id, state),
        name_fn=lambda webasto: webasto.output_aux2_name,
        entity_registry_enabled_default=True,
    ),
]


async def async_setup_entry(hass, entry: WebastoConfigEntry, async_add_devices):
    """Set up switches."""
    switches = []

    coordinator = entry.runtime_data.coordinator

    for id, device in coordinator.cloud.devices.items():
        LOGGER.debug("Setting up switches for device: %s", device.name)
        for swi in SWITCHES:
            LOGGER.debug("Testing '%s'", swi.name)
            if swi.key == "ventilation_mode" and not _supports_ventilation_mode(device):
                LOGGER.debug("Skipping ventilation mode: not supported by device")
                continue
            if (
                isinstance(swi.name_fn, type(None))
                or swi.name_fn(coordinator.cloud.devices[id]) is not False
            ):
                entity = WebastoConnectSwitch(id, swi, coordinator)
                LOGGER.debug(
                    "Adding switch '%s' with entity_id '%s'", swi.name, entity.entity_id
                )
                switches.append(entity)

    async_add_devices(switches)


class WebastoConnectSwitch(WebastoBaseEntity, SwitchEntity):
    """Representation of a Webasto Connect switch."""

    def __init__(
        self,
        device_id: int,
        description: WebastoConnectSwitchEntityDescription,
        coordinator: WebastoConnectUpdateCoordinator,
    ) -> None:
        """Initialize a Webasto Connect switch."""
        super().__init__(device_id, coordinator, description)

        self._handle_states()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._handle_states()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Unavailable when updates fail or the device is disconnected."""
        return self._coordinator_ok and self._is_device_connected

    def _handle_states(self) -> None:
        """Handle the switch states."""
        if not isinstance(self.entity_description.name_fn, type(None)):  # type: ignore
            self._attr_name = self.entity_description.name_fn(  # type: ignore
                self._device
            )

        self._attr_is_on = self.entity_description.value_fn(  # type: ignore
            self._device
        )

        if self.entity_description.key == "main_output":
            if self._device.is_ventilation:
                self._attr_icon = "mdi:fan" if self._attr_is_on else "mdi:fan-off"
            else:
                self._attr_icon = (
                    "mdi:radiator" if self._attr_is_on else "mdi:radiator-off"
                )
        elif self.entity_description.key == "ventilation_mode":
            self._attr_icon = "mdi:fan" if self._attr_is_on else "mdi:fan-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        LOGGER.debug("Turning on %s", self.entity_id)
        await self.coordinator.async_execute_cloud_call(
            self.entity_description.command_fn,  # type: ignore[arg-type]
            self._cloud,
            self._cloud.devices[self._device_id],
            True,
        )
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        LOGGER.debug("Turning off %s", self.entity_id)
        await self.coordinator.async_execute_cloud_call(
            self.entity_description.command_fn,  # type: ignore[arg-type]
            self._cloud,
            self._cloud.devices[self._device_id],
            False,
        )
        self.coordinator.async_update_listeners()
