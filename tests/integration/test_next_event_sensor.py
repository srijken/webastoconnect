"""The next event sensor with a real Home Assistant core."""

from collections.abc import Generator
from unittest.mock import PropertyMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .fake_webasto import DEVICE_ID, FakeWebastoCloud

ENTITY_ID = "sensor.camper_next_event"


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable entities that are disabled by default (like Home Assistant's fixture)."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


async def _setup(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    next_event: dict,
) -> None:
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    fake_cloud.devices[DEVICE_ID]["nextEvent"] = next_event
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_disabled_by_default(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """The sensor is a diagnostic entity that is disabled by default."""
    await _setup(hass, fake_cloud, config_entry, {})

    entry = er.async_get(hass).async_get(ENTITY_ID)
    assert entry is not None
    assert entry.unique_id == f"{DEVICE_ID}_next_event"
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category == "diagnostic"
    assert entry.translation_key == "next_event"
    assert hass.states.get(ENTITY_ID) is None


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.freeze_time("2026-11-17 22:37:00+00:00")
async def test_state_and_attributes(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """The state is the local time of day; attributes keep the raw details."""
    await _setup(
        hass,
        fake_cloud,
        config_entry,
        {"line": "OUTH", "type": "smart", "day": 4, "time": 1360},
    )

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "23:40"
    assert state.attributes["line"] == "Heater"
    assert state.attributes["type"] == "smart"
    assert state.attributes["time_utc"] == "22:40"
    assert state.attributes["day_raw"] == 4
    assert state.attributes["friendly_name"] == "Camper Next event"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.freeze_time("2026-11-17 22:37:00+00:00")
async def test_no_next_event(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """An empty `nextEvent` gives an unknown state without event attributes."""
    await _setup(hass, fake_cloud, config_entry, {})

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "unknown"
    for attribute in ("line", "type", "time_utc", "day_raw"):
        assert attribute not in state.attributes
