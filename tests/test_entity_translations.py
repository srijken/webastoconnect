"""Tests for translated entity names and icon translations.

Names and icons moved from code to ``translations/*.json`` and ``icons.json``.
Existing installs keep their entity IDs (stored in the entity registry) and
unique IDs (device ID + description key). These tests also check that a new
install with the English locale still gets the entity IDs it got before.
"""

import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.helpers.entity_platform import _async_derive_object_ids
from homeassistant.helpers.translation import recursive_flatten
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import slugify
from pywebasto import WebastoDevice

from custom_components.webastoconnect import migration
from custom_components.webastoconnect.binary_sensor import (
    BINARY_SENSORS,
    WebastoConnectBinarySensor,
)
from custom_components.webastoconnect.const import DOMAIN
from custom_components.webastoconnect.device_tracker import (
    TRACKER,
    WebastoConnectDeviceTracker,
)
from custom_components.webastoconnect.number import NUMBERS, WebastoConnectNumber
from custom_components.webastoconnect.sensor import SENSORS, WebastoConnectSensor
from custom_components.webastoconnect.switch import SWITCHES, WebastoConnectSwitch

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
DEVICE_ID = "WBX123"

PLATFORMS = [
    ("sensor", SENSORS, WebastoConnectSensor),
    ("binary_sensor", BINARY_SENSORS, WebastoConnectBinarySensor),
    ("switch", SWITCHES, WebastoConnectSwitch),
    ("number", NUMBERS, WebastoConnectNumber),
    ("device_tracker", [TRACKER], WebastoConnectDeviceTracker),
]
ALL_DESCRIPTIONS = [
    (domain, description)
    for domain, descriptions, _ in PLATFORMS
    for description in descriptions
]

# Entity IDs a new install of the 'Camper' device got before this change
# (has_entity_name with the English names that were in code). Only the main
# output and AUX1 exist on this device; their names come from the Webasto app.
EXPECTED_ENTITY_IDS = {
    ("sensor", "temperature"): "sensor.camper_temperature",
    ("sensor", "battery_voltage"): "sensor.camper_battery",
    ("sensor", "subscription_expiration"): "sensor.camper_subscription_expiration",
    ("sensor", "subscription_days_left"): "sensor.camper_subscription_days_left",
    ("sensor", "main_output_end_time"): "sensor.camper_heater_ends",
    ("sensor", "next_enabled_timer"): "sensor.camper_next_start",
    ("sensor", "next_event"): "sensor.camper_next_event",
    ("sensor", "last_seen"): "sensor.camper_last_seen",
    ("sensor", "geofence_radius"): "sensor.camper_geofence_radius",
    ("sensor", "alarm_state"): "sensor.camper_alarm_state",
    ("sensor", "subscription_status"): "sensor.camper_subscription_status",
    ("sensor", "default_output"): "sensor.camper_default_output",
    (
        "sensor",
        "signal_lost_shutdown_minutes",
    ): "sensor.camper_switch_off_after_signal_loss",
    ("sensor", "emulation_mode"): "sensor.camper_emulation_mode",
    (
        "sensor",
        "software_external_version",
    ): "sensor.camper_external_software_version",
    ("binary_sensor", "is_connected"): "binary_sensor.camper_connected",
    (
        "binary_sensor",
        "allow_location",
    ): "binary_sensor.camper_allow_location_services",
    (
        "binary_sensor",
        "low_voltage_warning",
    ): "binary_sensor.camper_low_battery_voltage",
    ("binary_sensor", "geofence_enabled"): "binary_sensor.camper_geofence",
    (
        "binary_sensor",
        "signal_lost_shutdown_enabled",
    ): "binary_sensor.camper_switch_off_on_signal_loss",
    ("switch", "main_output"): "switch.camper_heater",
    ("switch", "ventilation_mode"): "switch.camper_ventilation_mode",
    ("switch", "aux1_output"): "switch.camper_extra",
    ("number", "low_voltage_cutoff"): "number.camper_low_voltage_cutoff",
    ("number", "ext_temp_comp"): "number.camper_temperature_compensation",
    ("device_tracker", "devicetracker"): "device_tracker.camper_location",
}


def _load(name: str) -> dict:
    return json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))


def _languages() -> list[str]:
    return sorted(path.stem for path in (COMPONENT_DIR / "translations").glob("*.json"))


def _device(
    *, ventilation: bool = False, main_name: str = "Heater", aux1: bool = True
) -> WebastoDevice:
    """Return a pywebasto device like the 'Camper' one."""
    heater = {"line": "OUTH", "name": main_name, "state": "OFF", "icon": "heat"}
    vent = {"line": "OUTV", "name": "Fan", "state": "OFF", "icon": "vent"}
    outputs = [vent if ventilation else heater]
    if aux1:
        outputs.append({"line": "OUT1", "name": "Extra", "state": "OFF", "icon": "aux"})
    device = WebastoDevice(DEVICE_ID, "Camper")
    device.settings = {"settings_tab": []}
    device.last_data = {
        "temperature": "5C",
        "voltage": "12.6V",
        "location": {"state": "ON", "lat": 1, "lon": 2},
        "outputs": outputs,
        "disabled_outputs": [heater if ventilation else vent],
    }
    return device


def _coordinator(device: WebastoDevice) -> SimpleNamespace:
    return SimpleNamespace(
        config_entry=SimpleNamespace(entry_id="entry-1"),
        hass=SimpleNamespace(),
        cloud=SimpleNamespace(devices={DEVICE_ID: device}),
        device_names={},
        last_update_success=True,
    )


def _platform_data(domain: str, language: str) -> SimpleNamespace:
    """Return platform data with translations as Home Assistant loads them.

    The object ID language is English for the English locale (and the
    translated language for languages in NATIVE_ENTITY_IDS).
    """
    translations = recursive_flatten(
        f"component.{DOMAIN}.",
        {"entity": _load(f"translations/{language}.json")["entity"]},
    )
    return SimpleNamespace(
        domain=domain,
        platform_name=DOMAIN,
        platform_translations=translations,
        object_id_platform_translations=translations,
        default_language_platform_translations=recursive_flatten(
            f"component.{DOMAIN}.", {"entity": _load("translations/en.json")["entity"]}
        ),
        component_translations={},
        object_id_component_translations={},
    )


def _entity(domain, cls, description, device, language="en"):
    entity = cls(DEVICE_ID, description, _coordinator(device))
    entity.platform_data = _platform_data(domain, language)
    entity.internal_integration_suggested_object_id = None
    return entity


def _new_install_entity_id(domain: str, entity) -> str:
    """Return the entity ID the entity registry generates for a new entity."""
    platform = SimpleNamespace(entity_namespace=None, platform_name=DOMAIN)
    suggested_object_id, object_id_base = _async_derive_object_ids(entity, platform)
    assert suggested_object_id is None
    return f"{domain}.{slugify(f'{entity._device_name} {object_id_base}')}"


@pytest.mark.parametrize(
    ("domain", "description"),
    ALL_DESCRIPTIONS,
    ids=lambda value: getattr(value, "key", value),
)
def test_descriptions_use_translation_key_without_name_or_icon(
    domain, description
) -> None:
    """Names and icons come from translations, keyed by the description key."""
    assert description.translation_key == description.key
    assert description.name is UNDEFINED
    assert description.icon is None


@pytest.mark.parametrize("language", _languages())
def test_every_language_translates_every_entity(language: str) -> None:
    """Each translation file names every entity, with the same placeholders."""
    english = _load("translations/en.json")["entity"]
    entity = _load(f"translations/{language}.json")["entity"]

    expected = {
        (domain, description.translation_key)
        for domain, description in ALL_DESCRIPTIONS
    }
    found = {(domain, key) for domain, keys in entity.items() for key in keys}
    assert found == expected
    for domain, keys in entity.items():
        for key, value in keys.items():
            assert value["name"].strip()
            assert set(re.findall(r"{(\w+)}", value["name"])) == set(
                re.findall(r"{(\w+)}", english[domain][key]["name"])
            )


def test_icons_json_refers_to_existing_translation_keys() -> None:
    """Every icon entry belongs to an entity and has a default icon."""
    icons = _load("icons.json")["entity"]
    keys = {
        (domain, description.translation_key)
        for domain, description in ALL_DESCRIPTIONS
    }
    for domain, entries in icons.items():
        for key, entry in entries.items():
            assert (domain, key) in keys
            assert entry["default"].startswith("mdi:")
            for icon in entry.get("state", {}).values():
                assert icon.startswith("mdi:")
    # Only the main output icon stays in code: it depends on the mode.
    assert "main_output" not in icons["switch"]
    assert {key for domain, key in keys} - {
        key for entries in icons.values() for key in entries
    } == {"main_output", "aux1_output", "aux2_output"}


def test_icons_json_keeps_previous_state_icons() -> None:
    """State-dependent icons in icons.json match the icons from code."""
    icons = _load("icons.json")["entity"]
    assert icons["binary_sensor"]["is_connected"] == {
        "default": "mdi:wifi-off",
        "state": {"on": "mdi:wifi"},
    }
    assert icons["switch"]["ventilation_mode"] == {
        "default": "mdi:fan-off",
        "state": {"on": "mdi:fan"},
    }


def test_translation_files_keep_formatting_with_icons() -> None:
    """icons.json uses the repository's JSON formatting."""
    path = COMPONENT_DIR / "icons.json"
    content = path.read_text(encoding="utf-8")
    assert content == json.dumps(json.loads(content), indent=4) + "\n"


def test_legacy_names_cover_every_description() -> None:
    """The unique ID migration knows the old English name of every entity."""
    assert (
        set(migration.LEGACY_NAMES)
        == {(domain, description.key) for domain, description in ALL_DESCRIPTIONS}
        - migration.KEYS_WITHOUT_LEGACY_NAME
    )


@pytest.mark.parametrize(("domain", "descriptions", "cls"), PLATFORMS)
def test_new_english_install_keeps_entity_ids(domain, descriptions, cls) -> None:
    """A new install with the English locale gets the same entity IDs as before."""
    device = _device()
    for description in descriptions:
        key = (domain, description.key)
        if key not in EXPECTED_ENTITY_IDS:
            # AUX2 is not on this device: setup does not create it.
            assert description.name_fn(device) is False
            continue
        entity = _entity(domain, cls, description, device)

        assert _new_install_entity_id(domain, entity) == EXPECTED_ENTITY_IDS[key]


@pytest.mark.parametrize(("domain", "descriptions", "cls"), PLATFORMS)
def test_unique_ids_are_unchanged(domain, descriptions, cls) -> None:
    """Unique IDs stay device ID + description key."""
    device = _device()
    for description in descriptions:
        if (domain, description.key) not in EXPECTED_ENTITY_IDS:
            continue
        entity = _entity(domain, cls, description, device)

        assert entity.unique_id == f"{DEVICE_ID}_{description.key}"


def test_entity_ids_match_legacy_names() -> None:
    """English names only changed case, which does not change entity IDs."""
    for (domain, key), legacy_name in migration.LEGACY_NAMES.items():
        if key in {"main_output", "aux1_output", "aux2_output"}:
            continue
        english = _load("translations/en.json")["entity"][domain][key]["name"]
        english = english.format(output_name="Output")
        assert slugify(english) == slugify(legacy_name)


def _description(descriptions, key):
    return next(d for d in descriptions if d.key == key)


def test_english_names_are_translated() -> None:
    """Entities without a name from the Webasto app use the translated name."""
    device = _device()
    entity = _entity(
        "binary_sensor",
        WebastoConnectBinarySensor,
        _description(BINARY_SENSORS, "low_voltage_warning"),
        device,
    )

    assert entity.name == "Low battery voltage"


def test_german_names_are_translated_but_app_names_win() -> None:
    """Translated names follow the language; names from the app are kept."""
    device = _device()
    temperature = _entity(
        "sensor",
        WebastoConnectSensor,
        _description(SENSORS, "temperature"),
        device,
        "de",
    )
    main = _entity(
        "switch",
        WebastoConnectSwitch,
        _description(SWITCHES, "main_output"),
        device,
        "de",
    )
    end_time = _entity(
        "sensor",
        WebastoConnectSensor,
        _description(SENSORS, "main_output_end_time"),
        device,
        "de",
    )

    assert temperature.name == "Temperatur"
    assert main.name == "Heater"
    assert end_time.name == "Heater endet"


def test_output_names_from_app_win_over_translations() -> None:
    """Switch names set in the Webasto app are used as-is."""
    device = _device()
    main = _entity(
        "switch", WebastoConnectSwitch, _description(SWITCHES, "main_output"), device
    )
    aux1 = _entity(
        "switch", WebastoConnectSwitch, _description(SWITCHES, "aux1_output"), device
    )

    assert main.name == "Heater"
    assert aux1.name == "Extra"


def test_switch_uses_translation_when_app_name_is_empty() -> None:
    """Without a usable name from the cloud the translated name is used."""
    device = _device()
    description = _description(SWITCHES, "main_output")
    main = _entity("switch", WebastoConnectSwitch, description, device)
    assert main.name == "Heater"

    main.entity_description = type(description)(
        **{**vars(description), "name_fn": lambda _device: ""}
    )
    main.async_write_ha_state = Mock()
    main._handle_coordinator_update()

    assert not hasattr(main, "_attr_name")
    assert main.name == "Output"


def test_switch_name_follows_mode_change() -> None:
    """The main output name follows the output name of the active mode."""
    device = _device()
    main = _entity(
        "switch", WebastoConnectSwitch, _description(SWITCHES, "main_output"), device
    )
    main.async_write_ha_state = Mock()
    assert main.name == "Heater"
    assert main.icon == "mdi:radiator-off"

    device.last_data = _device(ventilation=True).last_data
    main._handle_coordinator_update()

    assert main.name == "Fan"
    assert main.icon == "mdi:fan-off"
    assert main.unique_id == f"{DEVICE_ID}_main_output"


def test_end_time_sensor_name_follows_output_name() -> None:
    """The end-time sensor name follows the main output name."""
    device = _device()
    sensor = _entity(
        "sensor",
        WebastoConnectSensor,
        _description(SENSORS, "main_output_end_time"),
        device,
    )
    sensor.async_write_ha_state = Mock()
    assert sensor.name == "Heater ends"

    sensor._handle_coordinator_update()
    sensor.async_write_ha_state.assert_not_called()

    device.last_data = _device(ventilation=True).last_data
    sensor._handle_coordinator_update()

    assert sensor.name == "Fan ends"
    sensor.async_write_ha_state.assert_called_once()


def test_entities_have_no_icon_in_code_except_main_output() -> None:
    """Icons come from icons.json, except the mode-dependent main output icon."""
    device = _device()
    for domain, descriptions, cls in PLATFORMS:
        for description in descriptions:
            if (domain, description.key) not in EXPECTED_ENTITY_IDS:
                continue
            entity = _entity(domain, cls, description, device)
            if description.key == "main_output":
                assert entity.icon == "mdi:radiator-off"
            else:
                assert entity.icon is None
