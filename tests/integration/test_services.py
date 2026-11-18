"""Actions of the integration with a real Home Assistant core."""

import json
from unittest.mock import PropertyMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.webastoconnect.const import (
    DOMAIN,
    SERVICE_CREATE_SMART_TIMER,
    SERVICE_CREATE_TIMER,
    SERVICE_DELETE_TIMER,
    SERVICE_UPDATE_SMART_TIMER,
    SERVICE_UPDATE_TIMER,
)

from .fake_webasto import DEVICE_ID, SIMPLE_TIMER, SMART_TIMER, FakeWebastoCloud


SAVED_SMART_TIMER = {**SMART_TIMER, "departure": SMART_TIMER["start"]}
NEXT_TIMER_ENTITY = "sensor.camper_next_start"


def _timer_saves(fake_cloud: FakeWebastoCloud, since: int) -> list[dict]:
    """Return the `timers2` payloads posted since request number `since`."""
    return [
        json.loads(payload)
        for method, path, payload in fake_cloud.requests[since:]
        if method == "POST" and path.endswith("/timers2")
    ]


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


@pytest.mark.freeze_time("2026-11-18 12:00:00+00:00")
async def test_create_smart_timer(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, device_id: str
) -> None:
    """create_smart_timer posts the full list with start and departure set."""
    requests = len(fake_cloud.requests)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CREATE_SMART_TIMER,
        {
            "device_id": device_id,
            "departure_time": "07:30",
            "max_duration_minutes": 40,
            "repeat_days": ["monday", "tuesday"],
            "location": {"latitude": 1.5, "longitude": 2.5},
        },
        blocking=True,
    )

    # 07:30 in Amsterdam (CET, UTC+1) is 06:30 UTC.
    new_timer = {
        "type": "smart",
        "start": 390,
        "departure": 390,
        "maxDuration": 2400,
        "comfortLevel": 5,
        "repeat": 1 | 2,
        "enabled": True,
        "location": {"lat": "1.5", "lon": "2.5"},
    }
    assert _timer_saves(fake_cloud, requests) == [
        {
            "output": "H",
            "timers": [SIMPLE_TIMER, SAVED_SMART_TIMER, new_timer],
        }
    ]


@pytest.mark.freeze_time("2026-11-18 12:00:00+00:00")
async def test_update_smart_timer(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, device_id: str
) -> None:
    """update_smart_timer index 0 is the first smart timer, not the simple one."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_UPDATE_SMART_TIMER,
        {
            "device_id": device_id,
            "timer_index": 0,
            "departure_time": "08:00",
            "max_duration_minutes": 20,
            "comfort_level": 6,
            "enabled": False,
        },
        blocking=True,
    )

    assert fake_cloud.timers("OUTH") == [
        SIMPLE_TIMER,
        {
            **SAVED_SMART_TIMER,
            "start": 420,
            "departure": 420,
            "maxDuration": 1200,
            "comfortLevel": 6,
            "enabled": False,
        },
    ]


async def test_update_smart_timer_out_of_range(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, device_id: str
) -> None:
    """There is one smart timer, so index 1 is rejected and nothing is saved."""
    requests = len(fake_cloud.requests)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_SMART_TIMER,
            {"device_id": device_id, "timer_index": 1, "enabled": False},
            blocking=True,
        )

    assert err.value.translation_key == "invalid_timer_index"
    assert _timer_saves(fake_cloud, requests) == []


async def test_update_timer_index_still_counts_simple_timers(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, device_id: str
) -> None:
    """update_timer index 0 keeps pointing at the simple timer."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_UPDATE_TIMER,
        {"device_id": device_id, "timer_index": 0, "enabled": True},
        blocking=True,
    )

    assert fake_cloud.timers("OUTH") == [
        {**SIMPLE_TIMER, "enabled": True},
        SAVED_SMART_TIMER,
    ]


async def test_delete_smart_timer(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, device_id: str
) -> None:
    """delete_timer with type smart removes the smart timer only."""
    requests = len(fake_cloud.requests)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DELETE_TIMER,
        {"device_id": device_id, "timer_index": 0, "type": "smart", "line": "heater"},
        blocking=True,
    )

    assert _timer_saves(fake_cloud, requests) == [
        {"output": "H", "timers": [SIMPLE_TIMER]}
    ]


async def test_delete_timer_default_is_simple(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, device_id: str
) -> None:
    """delete_timer without a type deletes the simple timer and keeps the smart one."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_DELETE_TIMER,
        {"device_id": device_id, "timer_index": 0},
        blocking=True,
    )

    assert fake_cloud.timers("OUTH") == [SAVED_SMART_TIMER]


@pytest.mark.freeze_time("2026-11-18 12:00:00+00:00")
async def test_next_timer_sensor_lists_smart_timer(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
) -> None:
    """The next start sensor shows the smart timer with its departure time."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(NEXT_TIMER_ENTITY)
    assert state is not None
    # The simple timer is disabled; the one-off smart timer departs 22:40 UTC.
    assert state.state == "2026-11-18T22:40:00+00:00"
    assert state.attributes["next_timer_type"] == "smart"
    assert [
        (timer["type"], timer["index"]) for timer in state.attributes["timers"]
    ] == [("simple", 0), ("smart", 0)]
    smart = state.attributes["timers"][1]
    assert smart["departure_hhmm_utc"] == "22:40"
    assert smart["max_duration"] == 1800
    assert smart["comfort_level"] == 5
    assert smart["has_location"] is False
