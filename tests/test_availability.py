"""Tests that entities report unavailability when cloud updates fail."""

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.webastoconnect.base import (
    WebastoConnectSensorEntityDescription,
    WebastoConnectSwitchEntityDescription,
)
from custom_components.webastoconnect.device_tracker import WebastoConnectDeviceTracker
from custom_components.webastoconnect.sensor import WebastoConnectSensor
from custom_components.webastoconnect.switch import WebastoConnectSwitch


def _sensor(coordinator_ok: bool) -> WebastoConnectSensor:
    entity = object.__new__(WebastoConnectSensor)
    entity.coordinator = SimpleNamespace(last_update_success=coordinator_ok)
    entity._device_id = 1
    entity._cloud = SimpleNamespace(devices={1: SimpleNamespace(value=21)})
    entity.entity_description = WebastoConnectSensorEntityDescription(
        key="temperature", name="Temperature", value_fn=lambda dev: dev.value
    )
    entity._attr_name = "Temperature"
    entity._attr_native_value = 21
    entity.async_write_ha_state = Mock()
    return entity


def test_sensor_writes_when_update_fails_with_unchanged_value() -> None:
    """A failed update must be written so the sensor shows unavailable."""
    entity = _sensor(coordinator_ok=False)

    entity._handle_coordinator_update()

    assert entity.available is False
    entity.async_write_ha_state.assert_called_once()


def test_sensor_writes_again_when_update_recovers() -> None:
    """Recovery after a failure is written once, then unchanged updates skip."""
    entity = _sensor(coordinator_ok=False)
    entity._handle_coordinator_update()

    entity.coordinator.last_update_success = True
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()

    assert entity.available is True
    assert entity.async_write_ha_state.call_count == 2


def test_switch_unavailable_when_update_fails() -> None:
    """Switches are unavailable when the coordinator update failed."""
    entity = object.__new__(WebastoConnectSwitch)
    entity.coordinator = SimpleNamespace(last_update_success=False)
    entity._device_id = 1
    entity._cloud = SimpleNamespace(devices={1: SimpleNamespace(is_connected=True)})
    entity.entity_description = WebastoConnectSwitchEntityDescription(
        key="aux1_output",
        name="AUX1",
        value_fn=lambda dev: False,
        command_fn=lambda *_: None,
    )

    assert entity.available is False


def test_tracker_unavailable_when_update_fails() -> None:
    """The tracker is unavailable when the coordinator update failed."""
    tracker = object.__new__(WebastoConnectDeviceTracker)
    tracker.coordinator = SimpleNamespace(last_update_success=False)
    tracker._device_id = 1
    tracker._cloud = SimpleNamespace(
        devices={1: SimpleNamespace(location={"lat": 1.0, "lon": 2.0})}
    )

    assert tracker.available is False
