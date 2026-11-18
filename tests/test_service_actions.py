"""Tests for action registration, target lookup and translated action errors."""

import ast
import json
from pathlib import Path
import string
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pywebasto.exceptions import (
    InvalidRequestException,
    TooManyRequestsException,
    UnauthorizedException,
)

import custom_components.webastoconnect as integration
from custom_components.webastoconnect import services
from custom_components.webastoconnect.const import (
    DOMAIN,
    SERVICE_CREATE_TIMER,
    SERVICE_DELETE_TIMER,
    SERVICE_UPDATE_TIMER,
)

COMPONENT_DIR = Path(integration.__file__).parent
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"
LANGUAGES = ("cs", "da", "de", "en", "es", "fi", "fr", "nb", "nl")


def _translations(language: str) -> dict:
    return json.loads((TRANSLATIONS_DIR / f"{language}.json").read_text("utf-8"))


def _hass_with_services() -> SimpleNamespace:
    return SimpleNamespace(
        services=SimpleNamespace(
            async_register=Mock(),
            async_remove=Mock(),
            has_service=Mock(return_value=False),
        )
    )


@pytest.mark.asyncio
async def test_async_setup_registers_each_action_once(monkeypatch) -> None:
    """async_setup registers the three actions with their schemas."""
    monkeypatch.setattr(integration, "async_register_card", AsyncMock())
    monkeypatch.setattr(integration, "async_remove_legacy_card", AsyncMock())
    hass = _hass_with_services()

    assert await integration.async_setup(hass, {}) is True

    registered = {
        call.args[1]: call.kwargs["schema"]
        for call in hass.services.async_register.call_args_list
    }
    assert hass.services.async_register.call_count == 3
    assert registered == {
        SERVICE_CREATE_TIMER: services._CREATE_TIMER_SCHEMA,
        SERVICE_UPDATE_TIMER: services._UPDATE_TIMER_SCHEMA,
        SERVICE_DELETE_TIMER: services._DELETE_TIMER_SCHEMA,
    }
    assert all(
        call.args[0] == DOMAIN for call in hass.services.async_register.call_args_list
    )


def test_config_schema_is_config_entry_only() -> None:
    """The integration cannot be set up from YAML."""
    assert callable(integration.CONFIG_SCHEMA)
    assert integration.CONFIG_SCHEMA({}) == {}


@pytest.mark.asyncio
async def test_entry_setup_and_unload_leave_actions_alone(monkeypatch) -> None:
    """Setting up and unloading the last entry keeps the actions registered."""
    hass = _hass_with_services()
    hass.data = {}
    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
        async_entries=Mock(return_value=[]),
    )
    close = AsyncMock()
    coordinator = SimpleNamespace(cloud=SimpleNamespace(close=close))

    async def fake_setup(hass_obj, config_entry):
        return coordinator

    monkeypatch.setattr(integration, "_async_setup", fake_setup)
    entry = SimpleNamespace(entry_id="entry-1")

    assert await integration.async_setup_entry(hass, entry) is True
    assert await integration.async_unload_entry(hass, entry) is True

    close.assert_awaited_once()
    hass.services.async_register.assert_not_called()
    hass.services.async_remove.assert_not_called()
    assert not hasattr(services, "async_unregister_services")


def _entry(state: ConfigEntryState, devices: dict | None = None) -> SimpleNamespace:
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        title="me@example.com",
        state=state,
    )
    if devices is not None:
        entry.runtime_data = SimpleNamespace(
            coordinator=SimpleNamespace(cloud=SimpleNamespace(devices=devices))
        )
    return entry


def _hass_with_device(monkeypatch, entry, ha_device) -> SimpleNamespace:
    registry = SimpleNamespace(
        async_get=lambda device_id: ha_device if device_id == "ha-device" else None
    )
    monkeypatch.setattr(services.dr, "async_get", lambda hass: registry)
    return SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=Mock(return_value=[entry]),
            async_get_entry=lambda entry_id: (
                entry if entry_id == entry.entry_id else None
            ),
        )
    )


def _ha_device() -> SimpleNamespace:
    return SimpleNamespace(
        config_entries={"entry-1"},
        identifiers={(DOMAIN, "123")},
        name="Car",
        name_by_user=None,
    )


@pytest.mark.parametrize(
    "state",
    [
        ConfigEntryState.NOT_LOADED,
        ConfigEntryState.SETUP_RETRY,
        ConfigEntryState.SETUP_ERROR,
    ],
)
@pytest.mark.asyncio
async def test_action_raises_when_entry_not_loaded(monkeypatch, state) -> None:
    """An action on a device whose entry is not loaded is a validation error."""
    cloud_device = SimpleNamespace(device_id="123")
    # Runtime data that outlived the entry must not be used.
    entry = _entry(state, devices={"123": cloud_device})
    hass = _hass_with_device(monkeypatch, entry, _ha_device())
    call = SimpleNamespace(data={"device_id": "ha-device", "timer_index": 0})

    with pytest.raises(ServiceValidationError) as exc_info:
        await services._async_handle_delete_timer(hass, call)

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "entry_not_loaded"
    assert exc_info.value.translation_placeholders == {"entry_title": "me@example.com"}


def test_lookup_resolves_device_of_loaded_entry(monkeypatch) -> None:
    """A loaded entry resolves the registry device to the cloud device."""
    cloud_device = SimpleNamespace(device_id="123")
    entry = _entry(ConfigEntryState.LOADED, devices={"123": cloud_device})
    hass = _hass_with_device(monkeypatch, entry, _ha_device())

    coordinator, device = services._coordinator_and_device(hass, "ha-device")

    assert device is cloud_device
    assert coordinator is entry.runtime_data.coordinator
    # The Webasto API device ID still works for existing automations.
    assert services._coordinator_and_device(hass, "123")[1] is cloud_device


def test_lookup_unknown_device_is_validation_error(monkeypatch) -> None:
    """An unknown device ID is a translated validation error."""
    entry = _entry(ConfigEntryState.LOADED, devices={})
    hass = _hass_with_device(monkeypatch, entry, _ha_device())

    with pytest.raises(ServiceValidationError) as exc_info:
        services._coordinator_and_device(hass, "nope")

    assert exc_info.value.translation_key == "device_not_found"
    assert exc_info.value.translation_placeholders == {"device_id": "nope"}


def test_lookup_device_no_longer_reported(monkeypatch) -> None:
    """A registry device the account no longer reports is named in the error."""
    entry = _entry(
        ConfigEntryState.LOADED, devices={"999": SimpleNamespace(device_id="999")}
    )
    hass = _hass_with_device(monkeypatch, entry, _ha_device())

    with pytest.raises(ServiceValidationError) as exc_info:
        services._coordinator_and_device(hass, "ha-device")

    assert exc_info.value.translation_key == "device_not_reported"
    assert exc_info.value.translation_placeholders == {"device_name": "Car"}


@pytest.mark.parametrize(
    ("error", "expected_key"),
    [
        (InvalidRequestException("raw response body"), "delete_timer_failed"),
        (UnauthorizedException("raw response body"), "delete_timer_failed"),
        (TooManyRequestsException("raw response body"), "rate_limited"),
    ],
)
@pytest.mark.asyncio
async def test_cloud_failures_are_translated(monkeypatch, error, expected_key) -> None:
    """Cloud errors become translated errors without the response body."""
    cloud_device = SimpleNamespace(device_id="123", is_ventilation=False)
    entry = _entry(ConfigEntryState.LOADED, devices={"123": cloud_device})

    async def execute(cloud_call, *args, **kwargs):
        return await cloud_call(*args, **kwargs)

    coordinator = entry.runtime_data.coordinator
    coordinator.cloud.get_timers = AsyncMock(side_effect=error)
    coordinator.cloud.save_timers = AsyncMock()
    coordinator.async_execute_cloud_call = execute
    hass = _hass_with_device(monkeypatch, entry, _ha_device())
    call = SimpleNamespace(data={"device_id": "ha-device", "timer_index": 0})

    with pytest.raises(HomeAssistantError) as exc_info:
        await services._async_handle_delete_timer(hass, call)

    assert not isinstance(exc_info.value, ServiceValidationError)
    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == expected_key
    assert not exc_info.value.translation_placeholders
    assert exc_info.value.__cause__ is error


@pytest.mark.asyncio
async def test_library_rejected_timer_is_validation_error(monkeypatch) -> None:
    """A timer the library rejects is a validation error."""
    cloud_device = SimpleNamespace(device_id="123", is_ventilation=False)
    entry = _entry(ConfigEntryState.LOADED, devices={"123": cloud_device})

    async def execute(cloud_call, *args, **kwargs):
        return await cloud_call(*args, **kwargs)

    coordinator = entry.runtime_data.coordinator
    coordinator.cloud.get_timers = AsyncMock(return_value=[])
    coordinator.cloud.save_timers = AsyncMock(
        side_effect=ValueError("start must be > 0")
    )
    coordinator.async_execute_cloud_call = execute
    hass = _hass_with_device(monkeypatch, entry, _ha_device())
    hass.config = SimpleNamespace(time_zone="UTC")
    call = SimpleNamespace(
        data={
            "device_id": "ha-device",
            "start_time": "00:00",
            "duration_minutes": 30,
            "repeat_days": [],
        }
    )

    with pytest.raises(ServiceValidationError) as exc_info:
        await services._async_handle_create_timer(hass, call)

    assert exc_info.value.translation_key == "invalid_timer"
    assert exc_info.value.translation_placeholders == {"error": "start must be > 0"}


def _translation_keys_in_code() -> set[str]:
    """Collect the exception translation keys that services.py uses."""
    tree = ast.parse((COMPONENT_DIR / "services.py").read_text("utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None)
            if name in {"_validation_error", "_cloud_error"} and node.args:
                if isinstance(node.args[0], ast.Constant):
                    keys.add(node.args[0].value)
            for keyword in node.keywords:
                if keyword.arg == "translation_key" and isinstance(
                    keyword.value, ast.Constant
                ):
                    keys.add(keyword.value.value)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if any(
                isinstance(target, ast.Name) and target.id == "translation_key"
                for target in node.targets
            ):
                keys.add(node.value.value)
    return keys


def test_every_exception_key_in_code_is_translated_in_english() -> None:
    """Each translation key raised by the actions exists in en.json."""
    keys = _translation_keys_in_code()
    exceptions = _translations("en")["exceptions"]

    assert len(keys) >= 20
    assert keys <= set(exceptions), keys - set(exceptions)
    assert set(exceptions) - keys == set()


def _placeholders(message: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(message) if field}


@pytest.mark.parametrize("language", LANGUAGES)
def test_translations_match_english(language: str) -> None:
    """Every language has the same action, selector and exception keys."""
    english = _translations("en")
    translated = _translations(language)

    for section in ("services", "selector", "exceptions"):
        assert set(translated[section]) == set(english[section]), section
    for service, data in english["services"].items():
        assert set(translated["services"][service]["fields"]) == set(data["fields"])
    assert translated["selector"]["weekday"]["options"].keys() == (
        english["selector"]["weekday"]["options"].keys()
    )
    for key, data in english["exceptions"].items():
        assert _placeholders(translated["exceptions"][key]["message"]) == (
            _placeholders(data["message"])
        ), key


def test_services_yaml_texts_live_in_translations() -> None:
    """services.yaml has no texts; every action and field is translated."""
    definitions = yaml.safe_load((COMPONENT_DIR / "services.yaml").read_text("utf-8"))
    english = _translations("en")["services"]

    assert set(definitions) == set(english)
    for service, definition in definitions.items():
        assert "name" not in definition
        assert "description" not in definition
        assert set(definition["fields"]) == set(english[service]["fields"])
        for field, field_definition in definition["fields"].items():
            assert "name" not in field_definition
            assert "description" not in field_definition
            texts = english[service]["fields"][field]
            assert texts["name"]
            assert texts["description"]


def test_translation_files_keep_their_formatting() -> None:
    """Translation files use 4-space indentation and end with a newline."""
    for language in LANGUAGES:
        text = (TRANSLATIONS_DIR / f"{language}.json").read_text("utf-8")
        data = json.loads(text)
        assert text == json.dumps(data, indent=4, ensure_ascii=False) + "\n"
