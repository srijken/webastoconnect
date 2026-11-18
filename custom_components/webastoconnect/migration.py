"""One-time entity registry migration to key-based unique IDs.

Up to config entry version 1.2, an entity's unique ID was
``slugify(f"{device_id}_{name}")`` with the display name at the time. For the
output switches and the end-time sensor that name comes from the output names
set in the Webasto app (and from the heating or ventilation mode), so a rename
created a new entity. From 1.3 the unique ID is ``f"{device_id}_{key}"``.

The mapping from an old unique ID to a description key needs the output names,
which only the cloud provides, so this runs during setup after connecting and
before the platforms add entities (see ``_async_setup``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import DOMAIN

LOGGER = logging.getLogger(__name__)

# Config entry minor version that has key-based unique IDs.
KEY_UNIQUE_ID_MINOR_VERSION = 3

MAIN_OUTPUT_LINES = ("OUTH", "OUTV")
# Description key -> (output lines, name pywebasto uses when the name is empty).
OUTPUT_SWITCH_LINES = {
    "main_output": (MAIN_OUTPUT_LINES, "Primary"),
    "aux1_output": (("OUT1",), "Output 1"),
    "aux2_output": (("OUT2",), "Output 2"),
}
END_TIME_KEY = "main_output_end_time"
END_TIME_FALLBACK_NAME = "Output ends"

# (domain, description key) -> the English name the entity had before names
# moved to translations. The pre-1.3 unique IDs were built from these names, so
# they must not change; translated names do not affect this migration.
LEGACY_NAMES: dict[tuple[str, str], str] = {
    ("sensor", "temperature"): "Temperature",
    ("sensor", "battery_voltage"): "Battery",
    ("sensor", "subscription_expiration"): "Subscription Expiration",
    ("sensor", "main_output_end_time"): END_TIME_FALLBACK_NAME,
    ("sensor", "next_enabled_timer"): "Next start",
    ("sensor", "last_seen"): "Last Seen",
    ("sensor", "geofence_radius"): "Geofence Radius",
    ("sensor", "alarm_state"): "Alarm State",
    ("sensor", "subscription_status"): "Subscription Status",
    ("sensor", "default_output"): "Default Output",
    ("sensor", "signal_lost_shutdown_minutes"): "Switch Off After Signal Loss",
    ("sensor", "emulation_mode"): "Emulation Mode",
    ("sensor", "software_external_version"): "External Software Version",
    ("binary_sensor", "is_connected"): "Connected",
    ("binary_sensor", "allow_location"): "Allow Location Services",
    ("binary_sensor", "low_voltage_warning"): "Low Battery Voltage",
    ("binary_sensor", "geofence_enabled"): "Geofence",
    ("binary_sensor", "signal_lost_shutdown_enabled"): "Switch Off On Signal Loss",
    ("switch", "main_output"): "Output",
    ("switch", "ventilation_mode"): "Ventilation Mode",
    ("switch", "aux1_output"): "AUX1",
    ("switch", "aux2_output"): "AUX2",
    ("number", "low_voltage_cutoff"): "Low Voltage Cutoff",
    ("number", "ext_temp_comp"): "Temperature Compensation",
    ("device_tracker", "devicetracker"): "Location",
}
# (domain, description key) of entities added after the 1.3 migration. They
# never had a name-based unique ID, so they have no legacy name.
KEYS_WITHOUT_LEGACY_NAME: frozenset[tuple[str, str]] = frozenset(
    {("sensor", "subscription_days_left")}
)


def key_unique_id(device_id: Any, key: str) -> str:
    """Return the unique ID of an entity: device ID and description key."""
    return f"{device_id}_{key}"


def _name_unique_id(device_id: Any, name: str) -> str:
    """Return the unique ID the code before 1.3 built from a display name."""
    return slugify(f"{device_id}_{name}")


@dataclass(frozen=True, slots=True)
class EntityTarget:
    """Every name one entity of a device could have had before 1.3."""

    domain: str
    key: str
    current_name: str | None
    names: frozenset[str]


def _descriptions() -> list[tuple[str, Any]]:
    """Return (domain, description) for every entity this integration creates."""
    # Imported here: the platform modules import the package itself.
    from .binary_sensor import BINARY_SENSORS
    from .device_tracker import TRACKER
    from .number import NUMBERS
    from .sensor import SENSORS
    from .switch import SWITCHES

    return [
        *(("sensor", description) for description in SENSORS),
        *(("binary_sensor", description) for description in BINARY_SENSORS),
        *(("switch", description) for description in SWITCHES),
        *(("number", description) for description in NUMBERS),
        ("device_tracker", TRACKER),
    ]


def _output_names(device: Any, lines: Iterable[str], default: str) -> set[str]:
    """Return the names of the outputs on the lines, as pywebasto reports them.

    The inactive heating or ventilation output is in ``disabled_outputs``.
    """
    names = {default}
    last_data = getattr(device, "last_data", None)
    if not isinstance(last_data, dict):
        return names
    for section in ("outputs", "disabled_outputs"):
        outputs = last_data.get(section)
        if not isinstance(outputs, list):
            continue
        for output in outputs:
            if isinstance(output, dict) and output.get("line") in lines:
                name = output.get("name")
                names.add(name if isinstance(name, str) and name else default)
    return names


def _end_time_name(output_name: str) -> str:
    """Return the end-time sensor name for a main output name."""
    if output_name.strip():
        return f"{output_name} ends"
    return END_TIME_FALLBACK_NAME


def entity_targets(device: Any) -> list[EntityTarget]:
    """Return the possible pre-1.3 names of every entity of a device."""
    targets = []
    for domain, description in _descriptions():
        legacy_name = LEGACY_NAMES.get((domain, description.key))
        names = {legacy_name}
        current_name = legacy_name
        name_fn = getattr(description, "name_fn", None)
        if name_fn is not None:
            try:
                current = name_fn(device)
            except AttributeError, KeyError, TypeError:
                current = None
            current_name = current if isinstance(current, str) else None
            if current_name is not None:
                names.add(current_name)

        if domain == "switch" and description.key in OUTPUT_SWITCH_LINES:
            lines, default = OUTPUT_SWITCH_LINES[description.key]
            names |= _output_names(device, lines, default)
        elif domain == "sensor" and description.key == END_TIME_KEY:
            output_name = getattr(device, "output_main_name", None)
            current_name = _end_time_name(
                output_name if isinstance(output_name, str) else ""
            )
            names.add(current_name)
            names |= {
                _end_time_name(name)
                for name in _output_names(device, MAIN_OUTPUT_LINES, "Primary")
            }

        targets.append(
            EntityTarget(
                domain=domain,
                key=description.key,
                current_name=current_name,
                names=frozenset(name for name in names if isinstance(name, str)),
            )
        )
    return targets


@dataclass(frozen=True, slots=True)
class _Candidate:
    entity_id: str
    unique_id: str
    # True when the old unique ID is the one the pre-1.3 code builds from the
    # current names: that entity is the one in use.
    current: bool


def plan_unique_id_migration(
    entries: Iterable[er.RegistryEntry],
    devices: Mapping[str, Any],
    entity_device_ids: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """Return {entity_id: new unique ID} for the entries that can be mapped.

    ``devices`` maps Webasto device IDs to their pywebasto devices.
    ``entity_device_ids`` maps entity IDs to the Webasto device ID of their
    device registry entry; it is only used when the unique ID itself does not
    identify the entity (unique IDs from before the device ID was in them).
    Entries that cannot be mapped unambiguously, and entries that would take a
    unique ID another entry holds or is also mapped to, are left out.
    """
    entity_device_ids = entity_device_ids or {}

    # (domain, old unique ID) -> {(new unique ID, current)}
    by_old_id: dict[tuple[str, str], set[tuple[str, bool]]] = defaultdict(set)
    # (device ID, domain, name) -> {new unique ID}
    by_name: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    new_ids: set[tuple[str, str]] = set()

    for device_id, device in devices.items():
        for target in entity_targets(device):
            new_id = key_unique_id(device_id, target.key)
            new_ids.add((target.domain, new_id))
            for name in target.names:
                current = name == target.current_name
                by_old_id[(target.domain, _name_unique_id(device_id, name))].add(
                    (new_id, current)
                )
                by_name[(str(device_id), target.domain, name)].add(new_id)

    entries = list(entries)
    held = {
        (entry.domain, entry.unique_id)
        for entry in entries
        if (entry.domain, entry.unique_id) in new_ids
    }
    candidates: dict[tuple[str, str], list[_Candidate]] = defaultdict(list)

    for entry in entries:
        domain = entry.domain
        if (domain, entry.unique_id) in new_ids:
            continue

        new_id: str | None = None
        current = False
        matches = by_old_id.get((domain, entry.unique_id), set())
        if len({new_id for new_id, _ in matches}) == 1:
            new_id = next(iter(matches))[0]
            current = any(is_current for _, is_current in matches)
        elif not matches and entry.original_name:
            device_id = entity_device_ids.get(entry.entity_id)
            if device_id is None and len(devices) == 1:
                device_id = str(next(iter(devices)))
            if device_id is not None:
                name_matches = by_name.get(
                    (str(device_id), domain, entry.original_name), set()
                )
                if len(name_matches) == 1:
                    new_id = next(iter(name_matches))

        if new_id is None:
            LOGGER.debug(
                "Leaving unique ID of %s unchanged: no single matching entity for '%s'",
                entry.entity_id,
                entry.unique_id,
            )
            continue
        candidates[(domain, new_id)].append(
            _Candidate(entry.entity_id, entry.unique_id, current)
        )

    plan: dict[str, str] = {}
    for (domain, new_id), found in candidates.items():
        if (domain, new_id) in held:
            winner = None
        elif len(found) == 1:
            winner = found[0]
        else:
            in_use = [candidate for candidate in found if candidate.current]
            winner = in_use[0] if len(in_use) == 1 else None
        for candidate in found:
            if candidate is winner:
                plan[candidate.entity_id] = new_id
            else:
                LOGGER.debug(
                    "Leaving unique ID of %s unchanged: '%s' is used by another entity",
                    candidate.entity_id,
                    new_id,
                )
    return plan


@callback
def _entity_device_ids(hass: HomeAssistant, entry_id: str) -> dict[str, str | None]:
    """Return {entity_id: Webasto device ID} from the device registry."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    result: dict[str, str | None] = {}
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry_id):
        if entity_entry.device_id is None:
            continue
        device_entry = device_registry.async_get(entity_entry.device_id)
        if device_entry is None:
            continue
        for domain, identifier in device_entry.identifiers:
            if domain == DOMAIN:
                result[entity_entry.entity_id] = identifier
                break
    return result


async def async_migrate_to_key_unique_ids(
    hass: HomeAssistant, entry_id: str, devices: Mapping[str, Any]
) -> None:
    """Move registry entries of the config entry to key-based unique IDs."""
    entity_registry = er.async_get(hass)
    plan = plan_unique_id_migration(
        er.async_entries_for_config_entry(entity_registry, entry_id),
        devices,
        _entity_device_ids(hass, entry_id),
    )

    @callback
    def _migrator(entity_entry: er.RegistryEntry) -> dict[str, Any] | None:
        new_id = plan.get(entity_entry.entity_id)
        if new_id is None:
            return None
        # async_update_entity raises on a unique ID that is in use. The holder
        # may be moved away later in this pass, so retry in the next pass.
        if entity_registry.async_get_entity_id(entity_entry.domain, DOMAIN, new_id):
            return None
        del plan[entity_entry.entity_id]
        LOGGER.debug(
            "Migrating unique ID of %s from '%s' to '%s'",
            entity_entry.entity_id,
            entity_entry.unique_id,
            new_id,
        )
        return {"new_unique_id": new_id}

    while plan:
        remaining = len(plan)
        await er.async_migrate_entries(hass, entry_id, _migrator)
        if len(plan) == remaining:
            break

    for entity_id, new_id in plan.items():
        LOGGER.debug(
            "Leaving unique ID of %s unchanged: '%s' is used by another entity",
            entity_id,
            new_id,
        )
