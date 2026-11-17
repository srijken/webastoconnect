"""Tests for status and settings entities backed by pywebasto device fields."""

from types import SimpleNamespace

import pytest

from custom_components.webastoconnect.binary_sensor import BINARY_SENSORS
from custom_components.webastoconnect.sensor import SENSORS
from custom_components.webastoconnect.switch import _supports_ventilation_mode

BINARY_SENSOR_FIELDS = {
    "low_voltage_warning": "low_voltage_warning",
    "geofence_enabled": "geofence_enabled",
    "signal_lost_shutdown_enabled": "signal_lost_shutdown_enabled",
}

SENSOR_FIELDS = {
    "last_seen": ("last_seen_seconds", 97),
    "geofence_radius": ("geofence_radius", 1.0),
    "alarm_state": ("alarm_state", "ON"),
    "subscription_status": ("subscription_status", "ok"),
    "default_output": ("default_output", "automatic"),
    "signal_lost_shutdown_minutes": ("signal_lost_shutdown_minutes", 10),
    "emulation_mode": ("emulation_mode", "thermoconnect"),
    "software_external_version": ("software_external_version", "141"),
}


def _description(descriptions, key):
    return next(description for description in descriptions if description.key == key)


@pytest.mark.parametrize(("key", "field"), BINARY_SENSOR_FIELDS.items())
def test_binary_sensor_reads_device_field(key: str, field: str) -> None:
    """Binary sensors should read their pywebasto device field."""
    description = _description(BINARY_SENSORS, key)

    assert description.value_fn(SimpleNamespace(**{field: True})) is True
    assert description.value_fn(SimpleNamespace(**{field: False})) is False


@pytest.mark.parametrize(("key", "field_and_value"), SENSOR_FIELDS.items())
def test_sensor_reads_device_field(key: str, field_and_value: tuple) -> None:
    """Sensors should read their pywebasto device field."""
    field, value = field_and_value
    description = _description(SENSORS, key)

    assert description.value_fn(SimpleNamespace(**{field: value})) == value


@pytest.mark.parametrize(
    ("descriptions", "key"),
    [(BINARY_SENSORS, key) for key in BINARY_SENSOR_FIELDS]
    + [(SENSORS, key) for key in SENSOR_FIELDS],
)
def test_entity_is_unknown_with_older_pywebasto(descriptions, key: str) -> None:
    """Entities should report no value when pywebasto lacks the field."""
    assert _description(descriptions, key).value_fn(SimpleNamespace()) is None


def test_low_voltage_warning_is_enabled_by_default() -> None:
    """The low voltage warning is useful by default; the rest is opt-in."""
    assert _description(
        BINARY_SENSORS, "low_voltage_warning"
    ).entity_registry_enabled_default


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        (SimpleNamespace(), True),
        (SimpleNamespace(output_main_features={}), True),
        (
            SimpleNamespace(output_main_features={"ventilation_mode_available": True}),
            True,
        ),
        (
            SimpleNamespace(output_main_features={"ventilation_mode_available": False}),
            False,
        ),
    ],
)
def test_supports_ventilation_mode(device, expected: bool) -> None:
    """Ventilation mode is hidden only when the device reports it unavailable."""
    assert _supports_ventilation_mode(device) is expected
