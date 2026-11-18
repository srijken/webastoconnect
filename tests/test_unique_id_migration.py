"""Tests for the one-time migration to key-based unique IDs (config entry 1.3)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
from pywebasto import WebastoDevice

import custom_components.webastoconnect as integration
from custom_components.webastoconnect import migration
from custom_components.webastoconnect.binary_sensor import BINARY_SENSORS
from custom_components.webastoconnect.const import DOMAIN
from custom_components.webastoconnect.device_tracker import TRACKER
from custom_components.webastoconnect.number import NUMBERS
from custom_components.webastoconnect.sensor import SENSORS
from custom_components.webastoconnect.switch import SWITCHES

DEVICE_ID = "WBX123"
ENTRY_ID = "entry-1"


def _device(*, ventilation: bool = False, aux1_name: str = "Extra") -> WebastoDevice:
    """Return a pywebasto device like the 'Camper' one, with outputs."""
    heater = {"line": "OUTH", "name": "Heater", "state": "OFF", "icon": "heat"}
    vent = {"line": "OUTV", "name": "Fan", "state": "OFF", "icon": "vent"}
    device = WebastoDevice(DEVICE_ID, "Camper")
    device.last_data = {
        "temperature": "5C",
        "voltage": "12.6V",
        "location": {"state": "ON", "lat": 1, "lon": 2},
        "outputs": [
            vent if ventilation else heater,
            {"line": "OUT1", "name": aux1_name, "state": "OFF", "icon": "aux"},
        ],
        "disabled_outputs": [heater if ventilation else vent],
    }
    return device


def _old_id(name: str) -> str:
    """Return the unique ID the code before 1.3 built for a display name."""
    return slugify(f"{DEVICE_ID}_{name}")


def _entry(
    entity_id: str,
    unique_id: str,
    original_name: str | None = None,
    device_id: str | None = "dev-1",
) -> SimpleNamespace:
    """Return an entity registry entry stub."""
    return SimpleNamespace(
        id=entity_id,
        entity_id=entity_id,
        domain=entity_id.split(".")[0],
        unique_id=unique_id,
        original_name=original_name,
        device_id=device_id,
        config_entry_id=ENTRY_ID,
    )


# The 'Camper' entities of a real installation, with their pre-1.3 unique IDs.
CAMPER = [
    _entry("switch.camper_heater", _old_id("Heater"), "Heater"),
    _entry("switch.camper_extra", _old_id("Extra"), "Extra"),
    _entry("sensor.camper_temperature", _old_id("Temperature"), "Temperature"),
    _entry("sensor.camper_battery", _old_id("Battery"), "Battery"),
    _entry("binary_sensor.camper_connected", _old_id("Connected"), "Connected"),
    _entry(
        "binary_sensor.camper_low_battery_voltage",
        _old_id("Low Battery Voltage"),
        "Low Battery Voltage",
    ),
    _entry("device_tracker.camper_location", _old_id("Location"), "Location"),
]


class FakeEntityRegistry:
    """Entity registry with the behaviour async_migrate_entries relies on."""

    def __init__(self, entries: list[SimpleNamespace]) -> None:
        """Store the entries by entity ID."""
        self._entries = {entry.entity_id: entry for entry in entries}
        self.entities = SimpleNamespace(
            get_entries_for_config_entry_id=lambda entry_id: [
                entry
                for entry in list(self._entries.values())
                if entry.config_entry_id == entry_id
            ],
            get_entry=lambda entry_id: self._entries.get(entry_id),
        )
        self.updates: list[tuple[str, str]] = []

    def async_get_entity_id(self, domain: str, platform: str, unique_id: str):
        """Return the entity ID holding a unique ID."""
        assert platform == DOMAIN
        for entry in self._entries.values():
            if entry.domain == domain and entry.unique_id == unique_id:
                return entry.entity_id
        return None

    def async_update_entity(self, entity_id: str, *, new_unique_id: str):
        """Update a unique ID; raises on a duplicate like Home Assistant does."""
        entry = self._entries[entity_id]
        if conflict := self.async_get_entity_id(entry.domain, DOMAIN, new_unique_id):
            raise ValueError(
                f"Unique id '{new_unique_id}' is already in use by {conflict}"
            )
        entry.unique_id = new_unique_id
        self.updates.append((entity_id, new_unique_id))
        return entry

    def unique_ids(self) -> dict[str, str]:
        """Return {entity_id: unique ID}."""
        return {
            entity_id: entry.unique_id for entity_id, entry in self._entries.items()
        }


@pytest.fixture
def registry(monkeypatch):
    """Install fake entity and device registries; returns a factory."""

    def _install(entries: list[SimpleNamespace]) -> FakeEntityRegistry:
        entity_registry = FakeEntityRegistry(entries)
        device_registry = SimpleNamespace(
            async_get=lambda device_id: (
                SimpleNamespace(identifiers={(DOMAIN, DEVICE_ID)})
                if device_id == "dev-1"
                else None
            )
        )
        monkeypatch.setattr(er, "async_get", lambda hass: entity_registry)
        monkeypatch.setattr(migration.dr, "async_get", lambda hass: device_registry)
        return entity_registry

    return _install


async def _migrate(devices=None) -> None:
    await migration.async_migrate_to_key_unique_ids(
        SimpleNamespace(), ENTRY_ID, devices or {DEVICE_ID: _device()}
    )


def _key_id(key: str) -> str:
    return f"{DEVICE_ID}_{key}"


@pytest.mark.asyncio
async def test_camper_entities_move_to_key_unique_ids(registry) -> None:
    """The real 'Camper' entities keep their entity_ids and get key IDs."""
    reg = registry([SimpleNamespace(**vars(entry)) for entry in CAMPER])

    await _migrate()

    assert reg.unique_ids() == {
        "switch.camper_heater": _key_id("main_output"),
        "switch.camper_extra": _key_id("aux1_output"),
        "sensor.camper_temperature": _key_id("temperature"),
        "sensor.camper_battery": _key_id("battery_voltage"),
        "binary_sensor.camper_connected": _key_id("is_connected"),
        "binary_sensor.camper_low_battery_voltage": _key_id("low_voltage_warning"),
        "device_tracker.camper_location": _key_id("devicetracker"),
    }


@pytest.mark.parametrize(
    ("domain", "description"),
    [
        *(
            ("sensor", d)
            for d in SENSORS
            if ("sensor", d.key) not in migration.KEYS_WITHOUT_LEGACY_NAME
        ),
        *(("binary_sensor", d) for d in BINARY_SENSORS),
        *(("switch", d) for d in SWITCHES if d.name_fn is None),
        *(("number", d) for d in NUMBERS),
        ("device_tracker", TRACKER),
    ],
    ids=lambda value: getattr(value, "key", value),
)
def test_every_description_name_maps_to_its_key(domain, description) -> None:
    """Each platform's description name-based ID maps to its key-based ID."""
    name = migration.LEGACY_NAMES[(domain, description.key)]
    entry = _entry(f"{domain}.camper_x", _old_id(name))

    plan = migration.plan_unique_id_migration([entry], {DEVICE_ID: _device()})

    assert plan == {entry.entity_id: _key_id(description.key)}


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Heater", "main_output"),
        ("Fan", "main_output"),
        ("Primary", "main_output"),
        ("Extra", "aux1_output"),
        ("Output 1", "aux1_output"),
        ("Output 2", "aux2_output"),
    ],
)
def test_output_switch_names_map_to_their_key(name: str, key: str) -> None:
    """Output names from the Webasto app and pywebasto fallbacks map to keys."""
    entry = _entry("switch.camper_x", _old_id(name), name)

    plan = migration.plan_unique_id_migration([entry], {DEVICE_ID: _device()})

    assert plan == {entry.entity_id: _key_id(key)}


def test_renamed_output_keeps_entity_and_id_no_longer_follows_name() -> None:
    """An output renamed in the app maps from its name-based ID to the key ID."""
    entry = _entry("switch.camper_lights", _old_id("Lights"), "Lights")

    plan = migration.plan_unique_id_migration(
        [entry], {DEVICE_ID: _device(aux1_name="Lights")}
    )

    assert plan == {"switch.camper_lights": _key_id("aux1_output")}


@pytest.mark.parametrize(
    ("name", "ventilation"),
    [
        ("Heater ends", False),
        ("Fan ends", False),
        ("Fan ends", True),
        ("Primary ends", False),
        ("Output ends", False),
    ],
)
def test_end_time_sensor_names_map_to_key(name: str, ventilation: bool) -> None:
    """The heating, ventilation and fallback end-time names map to one key."""
    entry = _entry("sensor.camper_fan_ends", _old_id(name), name)

    plan = migration.plan_unique_id_migration(
        [entry], {DEVICE_ID: _device(ventilation=ventilation)}
    )

    assert plan == {entry.entity_id: _key_id("main_output_end_time")}


def test_entry_id_based_unique_id_maps_by_original_name() -> None:
    """IDs from before the device ID was in them map by name and domain."""
    entry = _entry(
        "sensor.camper_temperature", slugify(f"Temperature_{ENTRY_ID}"), "Temperature"
    )

    plan = migration.plan_unique_id_migration(
        [entry], {DEVICE_ID: _device()}, {entry.entity_id: DEVICE_ID}
    )

    assert plan == {entry.entity_id: _key_id("temperature")}


def test_original_name_fallback_respects_domain() -> None:
    """The name fallback only matches descriptions of the entry's domain."""
    # "Geofence" is a binary sensor name; there is no such number.
    entry = _entry("number.camper_geofence", "legacy", "Geofence")

    assert migration.plan_unique_id_migration([entry], {DEVICE_ID: _device()}) == {}


@pytest.mark.asyncio
async def test_unmappable_entry_is_left_alone(registry, caplog) -> None:
    """Entries that match nothing keep their unique ID and are not removed."""
    caplog.set_level("DEBUG", logger=migration.__name__)
    stale = _entry("switch.camper_old_name", _old_id("Old Name"), "Old Name")
    other_device = _entry(
        "sensor.other_temperature", slugify("OTHER_Temperature"), None, None
    )
    reg = registry([stale, other_device])

    await _migrate({DEVICE_ID: _device(), "SECOND": _device()})

    assert reg.unique_ids() == {
        "switch.camper_old_name": _old_id("Old Name"),
        "sensor.other_temperature": slugify("OTHER_Temperature"),
    }
    assert "switch.camper_old_name" in caplog.text


@pytest.mark.asyncio
async def test_collision_keeps_entity_in_use(registry) -> None:
    """Of two entries for one key, the one the old code uses now is moved."""
    # Started once in heating mode and once in ventilation mode: the old code
    # created an entity per output name.
    orphan = _entry("switch.camper_heater", _old_id("Heater"), "Heater")
    in_use = _entry("switch.camper_fan", _old_id("Fan"), "Fan")
    reg = registry([orphan, in_use])

    # Ventilation mode: the old code names the main output "Fan" now.
    await _migrate({DEVICE_ID: _device(ventilation=True)})

    assert reg.unique_ids() == {
        "switch.camper_heater": _old_id("Heater"),
        "switch.camper_fan": _key_id("main_output"),
    }


@pytest.mark.asyncio
async def test_target_already_held_is_not_overwritten(registry) -> None:
    """An entry is not moved onto a key-based ID another entry already has."""
    held = _entry("sensor.camper_temperature", _key_id("temperature"), "Temperature")
    old = _entry("sensor.camper_temperature_2", _old_id("Temperature"), "Temperature")
    reg = registry([held, old])

    await _migrate()

    assert reg.updates == []


@pytest.mark.asyncio
async def test_ambiguous_collision_moves_neither(registry) -> None:
    """Two non-current entries for one key: neither is moved."""
    first = _entry("switch.camper_heater", _old_id("Heater"), "Heater")
    second = _entry("switch.camper_primary", _old_id("Primary"), "Primary")
    reg = registry([first, second])

    await _migrate({DEVICE_ID: _device(ventilation=True)})

    assert reg.updates == []


@pytest.mark.asyncio
async def test_migration_twice_is_a_no_op(registry) -> None:
    """A second run changes nothing."""
    reg = registry([SimpleNamespace(**vars(entry)) for entry in CAMPER])
    await _migrate()
    first = reg.unique_ids()
    reg.updates.clear()

    await _migrate()

    assert reg.updates == []
    assert reg.unique_ids() == first


def test_entity_unique_id_is_key_based() -> None:
    """Entities build their unique ID from the device ID and description key."""
    from custom_components.webastoconnect.switch import WebastoConnectSwitch

    device = _device()
    coordinator = SimpleNamespace(
        config_entry=SimpleNamespace(entry_id=ENTRY_ID),
        hass=SimpleNamespace(),
        cloud=SimpleNamespace(devices={DEVICE_ID: device}),
        device_names={},
        last_update_success=True,
    )
    aux1 = next(d for d in SWITCHES if d.key == "aux1_output")

    switch = WebastoConnectSwitch(DEVICE_ID, aux1, coordinator)

    assert switch.name == "Extra"
    assert switch.unique_id == _key_id("aux1_output")


def _setup_entry(minor_version: int) -> SimpleNamespace:
    return SimpleNamespace(entry_id=ENTRY_ID, version=1, minor_version=minor_version)


@pytest.mark.asyncio
async def test_setup_migrates_once_and_bumps_minor_version(monkeypatch) -> None:
    """Setup runs the migration for 1.2 entries and records 1.3."""
    migrate = AsyncMock()
    monkeypatch.setattr(integration, "async_migrate_to_key_unique_ids", migrate)
    update = Mock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
    entry = _setup_entry(2)
    devices = {DEVICE_ID: _device()}
    coordinator = SimpleNamespace(cloud=SimpleNamespace(devices=devices))

    await integration._async_migrate_to_key_unique_ids(hass, entry, coordinator)

    migrate.assert_awaited_once_with(hass, ENTRY_ID, devices)
    update.assert_called_once_with(entry, minor_version=3)

    migrate.reset_mock()
    update.reset_mock()
    await integration._async_migrate_to_key_unique_ids(
        hass, _setup_entry(3), coordinator
    )
    migrate.assert_not_awaited()
    update.assert_not_called()


@pytest.mark.asyncio
async def test_setup_postpones_migration_without_devices(monkeypatch) -> None:
    """Without devices nothing can be mapped, so the version stays at 1.2."""
    migrate = AsyncMock()
    monkeypatch.setattr(integration, "async_migrate_to_key_unique_ids", migrate)
    update = Mock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
    coordinator = SimpleNamespace(cloud=SimpleNamespace(devices={}))

    await integration._async_migrate_to_key_unique_ids(
        hass, _setup_entry(2), coordinator
    )

    migrate.assert_not_awaited()
    update.assert_not_called()


@pytest.mark.asyncio
async def test_migrate_entry_leaves_unique_id_step_to_setup() -> None:
    """async_migrate_entry accepts 1.2 entries without bumping them to 1.3."""
    update = Mock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))

    assert await integration.async_migrate_entry(hass, _setup_entry(2)) is True
    update.assert_not_called()
