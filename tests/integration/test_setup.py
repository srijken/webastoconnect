"""Set up, unload and fail setup with a real Home Assistant core."""

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pywebasto.exceptions import (
    ForbiddenException,
    InvalidRequestException,
    TooManyRequestsException,
)

from custom_components.webastoconnect.const import DOMAIN

from .fake_webasto import DEVICE_ID, FakeWebastoCloud

EXPECTED_ENTITY_IDS = {
    "binary_sensor.defender_allow_location_services",
    "binary_sensor.defender_connected",
    "binary_sensor.defender_geofence",
    "binary_sensor.defender_low_battery_voltage",
    "binary_sensor.defender_switch_off_on_signal_loss",
    "device_tracker.defender_location",
    "number.defender_low_voltage_cutoff",
    "number.defender_temperature_compensation",
    "sensor.defender_alarm_state",
    "sensor.defender_battery",
    "sensor.defender_default_output",
    "sensor.defender_emulation_mode",
    "sensor.defender_external_software_version",
    "sensor.defender_geofence_radius",
    "sensor.defender_heater_ends",
    "sensor.defender_last_seen",
    "sensor.defender_next_start",
    "sensor.defender_subscription_expiration",
    "sensor.defender_subscription_status",
    "sensor.defender_switch_off_after_signal_loss",
    "sensor.defender_temperature",
    "switch.defender_heater",
    "switch.defender_ventilation_mode",
}


async def test_setup_and_unload(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """Setup creates the device and its entities; unload closes the client."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    client = config_entry.runtime_data.coordinator.cloud
    assert client is fake_cloud.clients[-1]
    assert client.connected

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), config_entry.entry_id
    )
    assert device is not None
    assert device.name == "Defender"
    assert device.manufacturer == "Webasto"
    assert device.hw_version == "35.0"

    entries = er.async_entries_for_config_entry(
        er.async_get(hass), config_entry.entry_id
    )
    assert {entry.entity_id for entry in entries} == EXPECTED_ENTITY_IDS
    assert all(entry.unique_id.startswith(f"{DEVICE_ID}_") for entry in entries)
    assert all(entry.device_id == device.id for entry in entries)

    assert hass.states.get("switch.defender_heater").state == STATE_OFF
    temperature = hass.states.get("sensor.defender_temperature")
    assert temperature.state == "9"
    assert temperature.attributes["unit_of_measurement"] == "°C"
    assert hass.states.get("sensor.defender_battery").state == "10.1"
    assert hass.states.get("binary_sensor.defender_low_battery_voltage").state == (
        STATE_ON
    )
    assert hass.states.get("binary_sensor.defender_connected").state == STATE_ON

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert client.closed


async def test_switch_turn_on_sends_command(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """Turning on the heater switch sends the command and shows the new state."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.defender_heater"},
        blocking=True,
    )

    assert ("POST", f"/remote/client/fake-client-id/device/{DEVICE_ID}/cmd") in {
        (method, path) for method, path, _ in fake_cloud.requests
    }
    assert hass.states.get("switch.defender_heater").state == STATE_ON


@pytest.mark.xfail(
    reason=(
        "The device info passes sw_version as an int (152 from the web API "
        "settings); Home Assistant warns that this stops working in 2026.12"
    ),
    strict=True,
)
async def test_device_info_versions_are_strings(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The device registry receives the software version as a string."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), config_entry.entry_id
    )
    assert device.sw_version == "152"
    assert "passes a non-string value" not in caplog.text


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """A rejected password fails setup and asks the user to re-authenticate."""
    fake_cloud.password = "changed-elsewhere"

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = config_entry.async_get_active_flows(hass, {SOURCE_REAUTH})
    assert [flow["step_id"] for flow in flows] == ["reauth_confirm"]
    assert fake_cloud.clients[-1].closed


@pytest.mark.parametrize(
    "error",
    [
        TooManyRequestsException("rate limited"),
        ForbiddenException("forbidden"),
    ],
)
async def test_setup_temporary_failure_retries(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """Rate limiting and temporary errors make Home Assistant retry setup."""
    fake_cloud.connect_error = error

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not list(config_entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
    assert fake_cloud.clients[-1].closed


async def test_setup_rejected_request_is_an_error(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """A request the API rejects fails setup without retrying."""
    fake_cloud.connect_error = InvalidRequestException("rejected")

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert not list(config_entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
