"""Actions of the integration with a real Home Assistant core."""

import json

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.webastoconnect.const import DOMAIN, SERVICE_CREATE_TIMER

from .fake_webasto import DEVICE_ID, SIMPLE_TIMER, SMART_TIMER, FakeWebastoCloud


@pytest.fixture
async def device_id(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> str:
    """Set up the entry and return the device registry ID of the heater."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), config_entry.entry_id
    )
    assert device is not None
    return device.id


@pytest.mark.freeze_time("2026-11-18 12:00:00+00:00")
async def test_create_timer(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, device_id: str
) -> None:
    """create_timer appends a simple timer and keeps the smart timer."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_CREATE_TIMER,
        {
            "device_id": device_id,
            "start_time": "07:30",
            "duration_minutes": 45,
            "repeat_days": ["monday", "friday"],
        },
        blocking=True,
    )

    # 07:30 in Amsterdam (CET, UTC+1) is 06:30 UTC.
    assert fake_cloud.timers("OUTH") == [
        SIMPLE_TIMER,
        {**SMART_TIMER, "departure": SMART_TIMER["start"]},
        {
            "type": "simple",
            "start": 390,
            "duration": 2700,
            "repeat": 1 | 16,
            "enabled": True,
        },
    ]


async def test_create_timer_entry_not_loaded(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    device_id: str,
) -> None:
    """An action for a device of an unloaded entry raises a validation error."""
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    requests = len(fake_cloud.requests)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_TIMER,
            {"device_id": device_id, "start_time": "07:30", "duration_minutes": 45},
            blocking=True,
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "entry_not_loaded"
    assert len(fake_cloud.requests) == requests


async def test_create_timer_unknown_device(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, device_id: str
) -> None:
    """An unknown device raises a validation error."""
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_TIMER,
            {"device_id": "missing", "start_time": "07:30", "duration_minutes": 45},
            blocking=True,
        )

    assert err.value.translation_key == "device_not_found"


@pytest.mark.freeze_time("2026-11-18 12:00:00+00:00")
async def test_create_timer_on_ventilation_line(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, device_id: str
) -> None:
    """create_timer with line ventilation saves the timer on OUTV only."""
    fake_cloud.devices[DEVICE_ID]["outputs"].append(
        {"line": "OUTV", "name": "Ventilation", "state": "OFF", "timers": []}
    )
    requests = len(fake_cloud.requests)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CREATE_TIMER,
        {
            "device_id": device_id,
            "line": "ventilation",
            "start_time": "07:30",
            "duration_minutes": 20,
        },
        blocking=True,
    )

    saves = [
        json.loads(payload)
        for method, path, payload in fake_cloud.requests[requests:]
        if method == "POST" and path.endswith("/timers2")
    ]
    assert [save["output"] for save in saves] == ["V"]
    # 07:30 in Amsterdam (CET, UTC+1) is 06:30 UTC.
    assert fake_cloud.timers("OUTV") == [
        {
            "type": "simple",
            "start": 390,
            "duration": 1200,
            "repeat": 0,
            "enabled": True,
        },
    ]
    assert fake_cloud.timers("OUTH") == [SIMPLE_TIMER, SMART_TIMER]
