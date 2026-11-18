"""Switches for Webasto Connect."""

import logging
from typing import Any, cast

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from . import WebastoConfigEntry
from .api import WebastoConnectUpdateCoordinator
from .base import WebastoBaseEntity, WebastoConnectSwitchEntityDescription
from .const import DOMAIN

LOGGER = logging.getLogger(__name__)
# Switching is a cloud call: send one at a time.
PARALLEL_UPDATES = 1


def _supports_ventilation_mode(device: Any) -> bool:
    """Return False only when the device reports ventilation mode as unavailable."""
    features = getattr(device, "output_main_features", None)
    if not isinstance(features, dict):
        return True
    return features.get("ventilation_mode_available") is not False


# Line of the alarm control output. The Webasto app's geofence button switches
# it (EXPERIMENTAL, see the README).
ALARM_OUTPUT_LINE = "OUTA"
_UNSET = object()

# Output line behind each AUX switch. The main output (heater / ventilation)
# is left out on purpose: the inactive one of OUTH / OUTV is always listed
# under disabled_outputs.
AUX_OUTPUT_LINES = {"aux1_output": "OUT1", "aux2_output": "OUT2"}


def _outputs(device: Any, section: str) -> list[dict]:
    """Return the output dicts of one section of the latest device data."""
    data = getattr(device, "last_data", None)
    if not isinstance(data, dict):
        return []
    return [o for o in data.get(section) or [] if isinstance(o, dict)]


def alarm_output(device: Any) -> dict | None:
    """Return the alarm control output (OUTA) of the device, or None."""
    value = getattr(device, "alarm_output", _UNSET)
    if value is not _UNSET:
        return value if isinstance(value, dict) else None
    return next(
        (o for o in _outputs(device, "outputs") if o.get("line") == ALARM_OUTPUT_LINE),
        None,
    )


def geofence_active(device: Any) -> bool | None:
    """Return whether the geofence is active (EXPERIMENTAL).

    Uses pywebasto's geofence_active when the installed version has it. Older
    versions: the same rule from the raw data. The app's geofence button
    switches OUTA inverted, so OUTA "OFF" means the geofence is active.
    """
    value = getattr(device, "geofence_active", _UNSET)
    if value is not _UNSET:
        return value if isinstance(value, bool) else None
    output = alarm_output(device)
    if output is None:
        return None
    return {"OFF": True, "ON": False}.get(output.get("state"))


def output_disabled_in_portal(device: Any, line: str) -> bool:
    """Return True when the output is only listed under disabled_outputs.

    An output set to mode "Disabled" in the Webasto portal is listed there.
    """
    if any(o.get("line") == line for o in _outputs(device, "outputs")):
        return False
    return any(o.get("line") == line for o in _outputs(device, "disabled_outputs"))


async def _set_geofence(cloud: Any, device: Any, active: bool) -> None:
    """Activate or deactivate the geofence through pywebasto."""
    set_geofence = getattr(cloud, "set_geofence", None)
    if set_geofence is None:
        # pywebasto-ng 3.1.1 has no public command for OUTA.
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="geofence_not_supported",
        )
    await set_geofence(device, active)


SWITCHES = [
    WebastoConnectSwitchEntityDescription(
        key="main_output",
        translation_key="main_output",
        entity_category=None,
        value_fn=lambda webasto: cast(bool, webasto.output_main),
        command_fn=lambda webasto, id, state: webasto.set_output_main(id, state),
        name_fn=lambda webasto: webasto.output_main_name,
        entity_registry_enabled_default=True,
    ),
    WebastoConnectSwitchEntityDescription(
        key="ventilation_mode",
        translation_key="ventilation_mode",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda webasto: cast(bool, webasto.is_ventilation),
        command_fn=lambda webasto, id, state: webasto.ventilation_mode(id, state),
        entity_registry_enabled_default=False,
    ),
    WebastoConnectSwitchEntityDescription(
        key="aux1_output",
        translation_key="aux1_output",
        entity_category=None,
        value_fn=lambda webasto: cast(bool, webasto.output_aux1),
        command_fn=lambda webasto, id, state: webasto.set_output_aux1(id, state),
        name_fn=lambda webasto: webasto.output_aux1_name,
        entity_registry_enabled_default=True,
    ),
    WebastoConnectSwitchEntityDescription(
        key="aux2_output",
        translation_key="aux2_output",
        entity_category=None,
        value_fn=lambda webasto: cast(bool, webasto.output_aux2),
        command_fn=lambda webasto, id, state: webasto.set_output_aux2(id, state),
        name_fn=lambda webasto: webasto.output_aux2_name,
        entity_registry_enabled_default=True,
    ),
    WebastoConnectSwitchEntityDescription(
        key="geofence",
        translation_key="geofence",
        entity_category=None,
        value_fn=geofence_active,  # type: ignore[arg-type]  # None: unknown
        command_fn=_set_geofence,
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
            LOGGER.debug("Testing '%s'", swi.key)
            if swi.key == "ventilation_mode" and not _supports_ventilation_mode(device):
                LOGGER.debug("Skipping ventilation mode: not supported by device")
                continue
            if swi.key == "geofence" and alarm_output(device) is None:
                LOGGER.debug("Skipping geofence: device has no alarm output")
                continue
            if (
                isinstance(swi.name_fn, type(None))
                or swi.name_fn(coordinator.cloud.devices[id]) is not False
            ):
                entity = WebastoConnectSwitch(id, swi, coordinator)
                LOGGER.debug("Adding switch '%s'", swi.key)
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

        self._output_line = AUX_OUTPUT_LINES.get(description.key)
        self._disabled_in_portal = False
        self._handle_states()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._handle_states()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Unavailable on failed updates, disconnects or portal-disabled outputs."""
        return (
            self._coordinator_ok
            and self._is_device_connected
            and not getattr(self, "_disabled_in_portal", False)
        )

    def _update_disabled_in_portal(self) -> None:
        """Track whether this AUX output is disabled in the Webasto portal."""
        line = getattr(self, "_output_line", None)
        if line is None:
            return
        disabled = output_disabled_in_portal(self._device, line)
        if disabled and not getattr(self, "_disabled_in_portal", False):
            LOGGER.info(
                "Output %s is disabled in the Webasto portal (mode Disabled); "
                "its switch is unavailable until the output is enabled there",
                line,
            )
        self._disabled_in_portal = disabled

    def _handle_states(self) -> None:
        """Handle the switch states."""
        self._update_name()
        self._update_disabled_in_portal()

        self._attr_is_on = self.entity_description.value_fn(  # type: ignore
            self._device
        )

        # The main output icon follows the heating or ventilation mode, not
        # only the switch state. icons.json can only pick an icon by the
        # entity's own state (or a fixed translation_key), so this one stays
        # in code. The other switch icons are in icons.json.
        if self.entity_description.key == "main_output":
            if self._device.is_ventilation:
                self._attr_icon = "mdi:fan" if self._attr_is_on else "mdi:fan-off"
            else:
                self._attr_icon = (
                    "mdi:radiator" if self._attr_is_on else "mdi:radiator-off"
                )

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
