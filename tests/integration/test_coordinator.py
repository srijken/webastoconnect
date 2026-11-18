"""Coordinator updates and entity availability with a real Home Assistant core."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pywebasto.exceptions import InvalidResponseException

from custom_components.webastoconnect.api import SCAN_INTERVAL

from .fake_webasto import DEVICE_ID, FakeWebastoCloud

ENTITY_IDS = (
    "switch.defender_heater",
    "sensor.defender_temperature",
    "binary_sensor.defender_low_battery_voltage",
    "device_tracker.defender_location",
)


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.freeze_time("2026-11-18 12:00:00+00:00")
async def test_update_failure_and_recovery(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed update makes the entities unavailable until an update succeeds."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in ENTITY_IDS:
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    fake_cloud.request_error = InvalidResponseException("502 Bad Gateway")
    await _tick(hass, freezer, SCAN_INTERVAL)

    for entity_id in ENTITY_IDS:
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # Temporary failures are retried after 300 s, not at the scan interval.
    fake_cloud.request_error = None
    requests = len(fake_cloud.requests)
    await _tick(hass, freezer, SCAN_INTERVAL)
    assert len(fake_cloud.requests) == requests
    assert hass.states.get("switch.defender_heater").state == STATE_UNAVAILABLE

    # The heater was switched on in the app while the cloud was unreachable.
    fake_cloud.devices[DEVICE_ID]["outputs"][0]["state"] = "ON"
    fake_cloud.devices[DEVICE_ID]["temperature"] = "12C"
    await _tick(hass, freezer, timedelta(seconds=300) - SCAN_INTERVAL)

    assert hass.states.get("switch.defender_heater").state == STATE_ON
    assert hass.states.get("sensor.defender_temperature").state == "12"
    assert hass.states.get("binary_sensor.defender_low_battery_voltage").state == (
        STATE_ON
    )
    assert hass.states.get("device_tracker.defender_location").state != (
        STATE_UNAVAILABLE
    )


@pytest.mark.freeze_time("2026-11-18 12:00:00+00:00")
async def test_update_picks_up_state_changes(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A scheduled update writes the new cloud state to the entities."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.defender_heater").state == STATE_OFF

    fake_cloud.devices[DEVICE_ID]["outputs"][0]["state"] = "ON"
    fake_cloud.devices[DEVICE_ID]["vwarn"] = False
    await _tick(hass, freezer, SCAN_INTERVAL)

    assert hass.states.get("switch.defender_heater").state == STATE_ON
    assert hass.states.get("binary_sensor.defender_low_battery_voltage").state == (
        STATE_OFF
    )
