"""Unit tests for the geofence and portal-disabled output helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.exceptions import HomeAssistantError
import pytest
from pywebasto import WebastoDevice

from custom_components.webastoconnect.switch import (
    _set_geofence,
    alarm_output,
    geofence_active,
    output_disabled_in_portal,
)


def _device(outputs: list[dict], disabled: list[dict] | None = None) -> WebastoDevice:
    """Return a pywebasto device parsed from sanitized app data."""
    device = WebastoDevice("123", "Camper")
    device.app_data = {
        "assocStatus": "ok",
        "temperature": "9C",
        "voltage": "12.5V",
        "outputs": outputs,
        "disabled_outputs": disabled or [],
    }
    return device


HEATER = {"line": "OUTH", "name": "", "icon": "car_heat", "state": "OFF"}


@pytest.mark.parametrize(
    ("state", "expected"), [("OFF", True), ("ON", False), ("CONF", None)]
)
def test_geofence_active_from_raw_alarm_output(state, expected) -> None:
    """Without pywebasto support, OUTA "OFF" means the geofence is active."""
    device = _device([HEATER, {"line": "OUTA", "icon": "alert_on", "state": state}])

    assert alarm_output(device)["line"] == "OUTA"
    assert geofence_active(device) is expected


def test_geofence_active_without_alarm_output() -> None:
    """No OUTA output: no geofence state."""
    device = _device([HEATER], [{"line": "OUTA", "icon": "", "state": "OFF"}])

    assert alarm_output(device) is None
    assert geofence_active(device) is None


def test_library_properties_win() -> None:
    """A newer pywebasto's alarm_output and geofence_active are used as is."""
    output = {"line": "OUTA", "state": "ON"}
    device = SimpleNamespace(
        alarm_output=output,
        geofence_active=True,
        last_data={"outputs": []},
    )

    assert alarm_output(device) is output
    assert geofence_active(device) is True

    device = SimpleNamespace(alarm_output=None, geofence_active=None)
    assert alarm_output(device) is None
    assert geofence_active(device) is None


def test_output_disabled_in_portal() -> None:
    """Only outputs listed under disabled_outputs alone count as disabled."""
    aux = {"line": "OUT1", "name": "Extra", "icon": "aux", "state": "OFF"}
    vent = {"line": "OUTV", "name": "", "icon": "car_vent", "state": "OFF"}

    assert output_disabled_in_portal(_device([HEATER], [vent, aux]), "OUT1")
    assert not output_disabled_in_portal(_device([HEATER, aux], [vent]), "OUT1")
    assert not output_disabled_in_portal(_device([HEATER], [vent]), "OUT2")
    assert not output_disabled_in_portal(SimpleNamespace(last_data=None), "OUT1")


async def test_set_geofence_calls_library() -> None:
    """set_geofence of pywebasto is used when it exists."""
    cloud = SimpleNamespace(set_geofence=AsyncMock())
    device = object()

    await _set_geofence(cloud, device, True)

    cloud.set_geofence.assert_awaited_once_with(device, True)


async def test_set_geofence_without_library_support() -> None:
    """pywebasto-ng 3.1.1 has no set_geofence: a translated error, no call."""
    cloud = SimpleNamespace(_send_device_command=AsyncMock())

    with pytest.raises(HomeAssistantError) as exc_info:
        await _set_geofence(cloud, object(), False)

    assert exc_info.value.translation_key == "geofence_not_supported"
    cloud._send_device_command.assert_not_awaited()
