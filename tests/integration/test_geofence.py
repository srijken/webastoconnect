"""The experimental geofence switch and portal-disabled AUX outputs."""

from collections.abc import Generator
import logging
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .fake_webasto import DEVICE_ID, FakeWebastoCloud, FakeWebastoConnect

GEOFENCE = "switch.camper_geofence"
CMD_PATH = f"/remote/client/fake-client-id/device/{DEVICE_ID}/cmd"
# Shaped like the OUTA output in `/all?api_v=8` (2026-11-18, sanitized).
ALARM_OUTPUT = {"line": "OUTA", "name": "", "icon": "alert_on", "state": "ON"}


def _add_alarm_output(cloud: FakeWebastoCloud, state: str = "ON") -> None:
    device = cloud.devices[DEVICE_ID]
    device["outputs"].append({**ALARM_OUTPUT, "state": state})
    device["alarms"] = state


def _cmd_payloads(cloud: FakeWebastoCloud) -> list[Any]:
    return [payload for method, path, payload in cloud.requests if path == CMD_PATH]


@pytest.fixture
def library_with_set_geofence() -> Generator[None]:
    """Give the fake client the set_geofence of a newer pywebasto."""

    async def set_geofence(self, device, active: bool) -> None:
        await self._send_device_command(device, "OUT A OFF" if active else "OUT A ON")
        await self.update(device_id=device.device_id, force=True)

    FakeWebastoConnect.set_geofence = set_geofence  # type: ignore[attr-defined]
    yield
    del FakeWebastoConnect.set_geofence  # type: ignore[attr-defined]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_no_geofence_switch_without_alarm_output(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """Devices without an OUTA output get no geofence switch."""
    await _setup(hass, config_entry)

    assert hass.states.get(GEOFENCE) is None


@pytest.mark.parametrize(
    ("outa_state", "expected"), [("ON", STATE_OFF), ("OFF", STATE_ON)]
)
async def test_geofence_state_is_inverse_of_alarm_output(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    outa_state: str,
    expected: str,
) -> None:
    """OUTA "OFF" means the geofence is active."""
    _add_alarm_output(fake_cloud, outa_state)
    await _setup(hass, config_entry)

    state = hass.states.get(GEOFENCE)
    assert state is not None
    assert state.state == expected
    assert state.attributes["friendly_name"] == "Camper Geofence"


async def test_geofence_is_read_only_with_pywebasto_3_1_1(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """Without set_geofence in pywebasto, switching raises and sends nothing."""
    _add_alarm_output(fake_cloud)
    await _setup(hass, config_entry)
    assert not hasattr(config_entry.runtime_data.coordinator.cloud, "set_geofence")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": GEOFENCE}, blocking=True
        )

    assert exc_info.value.translation_key == "geofence_not_supported"
    assert _cmd_payloads(fake_cloud) == []
    assert hass.states.get(GEOFENCE).state == STATE_OFF


@pytest.mark.usefixtures("library_with_set_geofence")
async def test_geofence_switch_uses_set_geofence(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """With a newer pywebasto, on/off call set_geofence and follow OUTA."""
    _add_alarm_output(fake_cloud)
    await _setup(hass, config_entry)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": GEOFENCE}, blocking=True
    )
    assert _cmd_payloads(fake_cloud) == ["OUT A OFF"]
    assert hass.states.get(GEOFENCE).state == STATE_ON

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": GEOFENCE}, blocking=True
    )
    assert _cmd_payloads(fake_cloud) == ["OUT A OFF", "OUT A ON"]
    assert hass.states.get(GEOFENCE).state == STATE_OFF


async def test_aux_output_disabled_in_portal_is_unavailable(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """OUT1 in mode Disabled (only under disabled_outputs) is unavailable."""
    device = fake_cloud.devices[DEVICE_ID]
    device["disabled_outputs"] = [
        {"line": "OUTV", "name": "", "icon": "car_vent", "state": "OFF"},
        {"line": "OUT1", "name": "Extra", "icon": "aux", "state": "OFF"},
    ]
    caplog.set_level(logging.INFO, logger="custom_components.webastoconnect")
    await _setup(hass, config_entry)

    assert hass.states.get("switch.camper_extra").state == STATE_UNAVAILABLE
    assert "OUT1 is disabled in the Webasto portal" in caplog.text
    # The heater stays available although OUTV is under disabled_outputs.
    assert hass.states.get("switch.camper_heater").state == STATE_OFF

    # Enabled again in the portal: the output moves to outputs.
    device["disabled_outputs"] = device["disabled_outputs"][:1]
    device["outputs"].append(
        {"line": "OUT1", "name": "Extra", "icon": "aux", "state": "ON"}
    )
    await config_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("switch.camper_extra").state == STATE_ON
