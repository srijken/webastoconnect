"""Tests for diagnostics payload shaping and redaction."""

from types import SimpleNamespace

import pytest

from custom_components.webastoconnect.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.mark.asyncio
async def test_diagnostics_keeps_api_payload_and_redacts_sensitive_fields() -> None:
    """Diagnostics should include API payloads without dumping the full private object."""
    device = SimpleNamespace(
        device_id=123,
        name="My Heater",
        last_data={
            "location": {"lat": 55.5, "lon": 12.3},
            "outputs": [],
        },
        settings={
            "general": {"acc_email": "user@example.com"},
        },
        dev_data={"subscription": {"expiration": 1}},
        temperature=10,
        voltage=12.4,
        is_ventilation=False,
        output_main=True,
        output_aux1=False,
        output_aux2=False,
        temperature_unit="°C",
        _WebastoDevice__internal_only="should_not_be_exposed",
    )

    hass = SimpleNamespace()
    entry = SimpleNamespace(
        entry_id="entry-1",
        runtime_data=SimpleNamespace(
            coordinator=SimpleNamespace(cloud=SimpleNamespace(devices={123: device}))
        ),
        as_dict=lambda: {
            "title": "user@example.com",
            "unique_id": "user@example.com",
            "data": {"email": "user@example.com", "password": "secret"},
        },
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    payload = diagnostics["devices"]["device_1"]

    assert payload["api_payload"]["last_data"]["location"]["lat"] == "**REDACTED**"
    assert payload["api_payload"]["last_data"]["location"]["lon"] == "**REDACTED**"
    assert payload["api_payload"]["settings"]["general"]["acc_email"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["email"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["password"] == "**REDACTED**"
    assert diagnostics["entry"]["title"] == "**REDACTED**"
    assert diagnostics["entry"]["unique_id"] == "**REDACTED**"
    assert "_WebastoDevice__internal_only" not in payload


@pytest.mark.asyncio
async def test_diagnostics_redacts_device_id_check_id_and_phone_numbers() -> None:
    """Diagnostics should not expose device ids, check ids or SMS phone numbers."""
    device_data = {
        "id": "1234567890",
        "check_id": "abcd",
        "outputs": [],
        "term-approvals": [{"id": "tos", "approved": False}],
    }
    device = SimpleNamespace(
        device_id="1234567890",
        name="1234567890",
        last_data=device_data,
        settings={
            "settings_tab": [
                {
                    "group": "user_permissions",
                    "options": [
                        {"key": "SMS_users", "type": "list", "value": ["+31600000000"]}
                    ],
                },
                {
                    "group": "general",
                    "options": [{"key": "low_voltage_cutoff", "value": 10.3}],
                },
            ]
        },
        dev_data=device_data,
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        runtime_data=SimpleNamespace(
            coordinator=SimpleNamespace(
                cloud=SimpleNamespace(devices={"1234567890": device})
            )
        ),
        as_dict=lambda: {"data": {}},
    )

    diagnostics = await async_get_config_entry_diagnostics(SimpleNamespace(), entry)

    assert "1234567890" not in str(diagnostics)
    assert "abcd" not in str(diagnostics)
    assert "+31600000000" not in str(diagnostics)

    payload = diagnostics["devices"]["device_1"]
    assert payload["device_id"] == "**REDACTED**"
    assert payload["name"] == "**REDACTED**"
    settings = payload["api_payload"]["settings"]["settings_tab"]
    assert settings[0]["options"][0]["value"] == "**REDACTED**"
    # Unrelated settings and nested ids stay readable for bug reports.
    assert settings[1]["options"][0]["value"] == 10.3
    last_data = payload["api_payload"]["last_data"]
    assert last_data["term-approvals"][0]["id"] == "tos"
    # The original device data is not modified.
    assert device_data["id"] == "1234567890"
