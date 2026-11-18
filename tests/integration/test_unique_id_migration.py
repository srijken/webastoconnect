"""Unique ID migration (config entry 1.2 -> 1.3) on a real entity registry."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.webastoconnect.const import DOMAIN

from .fake_webasto import DEVICE_ID, EMAIL, PASSWORD, FakeWebastoCloud

# (domain, name the entity had before 1.3, entity_id, description key)
OLD_ENTITIES = [
    ("sensor", "Temperature", "sensor.defender_temperature", "temperature"),
    (
        "binary_sensor",
        "Low Battery Voltage",
        "binary_sensor.defender_low_battery_voltage",
        "low_voltage_warning",
    ),
    # The main output switch was named after the output in the Webasto app.
    ("switch", "Heater", "switch.defender_heater", "main_output"),
    # The end-time sensor was named after the main output too.
    ("sensor", "Heater ends", "sensor.defender_heater_ends", "main_output_end_time"),
    ("device_tracker", "Location", "device_tracker.defender_location", "devicetracker"),
]


async def test_setup_migrates_name_based_unique_ids(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud
) -> None:
    """Old name-based unique IDs move to key-based ones; entity_ids are kept."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL.lower(),
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, name, entity_id, _ in OLD_ENTITIES:
        registry.async_get_or_create(
            domain,
            DOMAIN,
            slugify(f"{DEVICE_ID}_{name}"),
            config_entry=entry,
            suggested_object_id=entity_id.split(".", 1)[1],
            original_name=name,
        )
    # An entity of another integration with the same unique ID is untouched.
    other = registry.async_get_or_create(
        "sensor", "other", slugify(f"{DEVICE_ID}_Temperature")
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 3
    for domain, _, entity_id, key in OLD_ENTITIES:
        assert (
            registry.async_get_entity_id(domain, DOMAIN, f"{DEVICE_ID}_{key}")
            == entity_id
        )
        assert hass.states.get(entity_id) is not None
    assert registry.async_get(other.entity_id).unique_id == other.unique_id

    # No duplicates were created next to the migrated entities.
    entity_ids = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert not any(entity_id.endswith("_2") for entity_id in entity_ids)


async def test_unique_id_migration_runs_once(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """An entry at 1.3 is not migrated again, even with a name-based unique ID."""
    registry = er.async_get(hass)
    leftover = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        slugify(f"{DEVICE_ID}_Battery"),
        config_entry=config_entry,
        suggested_object_id="old_battery",
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert registry.async_get(leftover.entity_id).unique_id == leftover.unique_id
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_ID}_battery_voltage")
        == "sensor.defender_battery"
    )


async def test_unique_id_migration_postponed_without_devices(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud
) -> None:
    """Without devices from the cloud nothing is mapped and 1.2 is kept."""
    fake_cloud.devices.clear()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL.lower(),
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
