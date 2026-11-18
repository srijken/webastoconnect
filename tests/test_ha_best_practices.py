"""Tests for Home Assistant conventions: manifest, entities and devices."""

from datetime import UTC, date, datetime
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfElectricPotential

import custom_components.webastoconnect as integration
from custom_components.webastoconnect.base import WebastoBaseEntity
from custom_components.webastoconnect.number import NUMBERS
from custom_components.webastoconnect.sensor import (
    DEFAULT_OUTPUT_OPTIONS,
    EMULATION_MODE_OPTIONS,
    SENSORS,
)
from homeassistant.helpers.entity import EntityDescription

COMPONENT_DIR = Path(integration.__file__).parent


def _description(descriptions, key):
    return next(description for description in descriptions if description.key == key)


def test_manifest_declares_integration_type_and_loggers() -> None:
    """The manifest names the integration type and the library logger."""
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text())

    assert manifest["integration_type"] == "hub"
    assert manifest["loggers"] == ["pywebasto"]


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("sensor", 0),
        ("binary_sensor", 0),
        ("device_tracker", 0),
        ("switch", 1),
        ("number", 1),
    ],
)
def test_platforms_set_parallel_updates(platform: str, expected: int) -> None:
    """Read-only platforms do not limit updates; actions run one at a time."""
    module = importlib.import_module(f"custom_components.webastoconnect.{platform}")

    assert expected == module.PARALLEL_UPDATES


def test_subscription_expiration_is_a_date() -> None:
    """The subscription expiration sensor reports a date."""
    description = _description(SENSORS, "subscription_expiration")

    assert description.device_class is SensorDeviceClass.DATE
    webasto = SimpleNamespace(
        subscription_expiration=datetime(2027, 3, 31, 22, 0, tzinfo=UTC)
    )
    assert description.value_fn(webasto) == date(2027, 3, 31)
    assert description.value_fn(SimpleNamespace(subscription_expiration=None)) is None
    assert description.value_fn(SimpleNamespace()) is None


def test_voltage_units_use_constants() -> None:
    """Voltage entities use the unit constant and the voltage device class."""
    battery = _description(SENSORS, "battery_voltage")
    cutoff = _description(NUMBERS, "low_voltage_cutoff")

    assert battery.native_unit_of_measurement == UnitOfElectricPotential.VOLT
    assert cutoff.native_unit_of_measurement == UnitOfElectricPotential.VOLT
    assert cutoff.device_class is NumberDeviceClass.VOLTAGE


@pytest.mark.parametrize(
    ("key", "field", "options"),
    [
        ("default_output", "default_output", DEFAULT_OUTPUT_OPTIONS),
        ("emulation_mode", "emulation_mode", EMULATION_MODE_OPTIONS),
    ],
)
def test_enum_sensors_report_known_options_only(key, field, options) -> None:
    """Enum sensors report documented values and None for unknown ones."""
    description = _description(SENSORS, key)

    assert description.device_class is SensorDeviceClass.ENUM
    assert description.options == options
    for option in options:
        assert description.value_fn(SimpleNamespace(**{field: option})) == option
    assert description.value_fn(SimpleNamespace(**{field: "something_new"})) is None
    assert description.value_fn(SimpleNamespace(**{field: None})) is None


def test_documented_enum_options() -> None:
    """Options match the values documented in the pywebasto protocol notes."""
    assert DEFAULT_OUTPUT_OPTIONS == [
        "automatic",
        "output1",
        "OUTA",
        "webasto_heater",
        "webasto_ventilation",
    ]
    assert EMULATION_MODE_OPTIONS == [
        "wb_auto_detect",
        "thermoconnect",
        "telestart",
        "telestart_special",
        "timer",
    ]


def test_alarm_state_stays_a_plain_sensor() -> None:
    """The alarm state values are not all known, so it is not an enum."""
    description = _description(SENSORS, "alarm_state")

    assert description.device_class is None
    assert description.options is None
    assert description.value_fn(SimpleNamespace(alarm_state="NEW")) == "NEW"


def _entity(settings: dict | None) -> WebastoBaseEntity:
    device = SimpleNamespace(
        device_id="123", name="Car", app_data={}, settings=settings
    )
    coordinator = SimpleNamespace(
        config_entry=None,
        hass=None,
        cloud=SimpleNamespace(devices={"123": device}),
        device_names={},
    )
    return WebastoBaseEntity(
        "123", coordinator, EntityDescription(key="test", name="Test")
    )


def test_device_info_versions_are_none_when_missing() -> None:
    """Missing hardware and software versions are left empty."""
    device_info = _entity({})._attr_device_info

    assert device_info["hw_version"] is None
    assert device_info["sw_version"] is None


def test_device_info_versions_from_settings() -> None:
    """Known hardware and software versions are shown."""
    device_info = _entity({"hw_version": "2.1", "sw_version": "4.7"})._attr_device_info

    assert device_info["hw_version"] == "2.1"
    assert device_info["sw_version"] == "4.7"


def _loaded_entry(device_ids) -> SimpleNamespace:
    return SimpleNamespace(
        runtime_data=SimpleNamespace(
            coordinator=SimpleNamespace(
                cloud=SimpleNamespace(devices=dict.fromkeys(device_ids))
            )
        )
    )


@pytest.mark.asyncio
async def test_remove_device_allowed_only_for_stale_devices() -> None:
    """Devices the account still reports cannot be removed; stale ones can."""
    entry = _loaded_entry(["123"])
    current = SimpleNamespace(identifiers={("webastoconnect", "123")})
    stale = SimpleNamespace(identifiers={("webastoconnect", "456")})

    assert not await integration.async_remove_config_entry_device(None, entry, current)
    assert await integration.async_remove_config_entry_device(None, entry, stale)


@pytest.mark.asyncio
async def test_remove_device_refused_when_entry_not_loaded() -> None:
    """Without a loaded entry the device list is unknown, so keep the device."""
    stale = SimpleNamespace(identifiers={("webastoconnect", "456")})

    assert not await integration.async_remove_config_entry_device(
        None, SimpleNamespace(), stale
    )


def _translations() -> dict[str, dict]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((COMPONENT_DIR / "translations").glob("*.json"))
    }


@pytest.mark.parametrize("language", sorted(_translations()))
def test_translations_have_reconfigure_step(language: str) -> None:
    """Every language translates the reconfigure step and its abort reason."""
    config = _translations()[language]["config"]
    step = config["step"]["reconfigure"]

    assert step["title"]
    assert "{email}" in step["description"]
    assert step["data"]["password"]
    assert config["abort"]["reconfigure_successful"]


@pytest.mark.parametrize("language", sorted(_translations()))
def test_translation_files_keep_formatting(language: str) -> None:
    """Translation files use 4-space JSON with a trailing newline."""
    path = COMPONENT_DIR / "translations" / f"{language}.json"
    text = path.read_text(encoding="utf-8")

    assert text == json.dumps(json.loads(text), indent=4, ensure_ascii=False) + "\n"
