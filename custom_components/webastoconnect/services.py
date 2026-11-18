"""Service handlers for Webasto timer operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pywebasto.exceptions import (
    ForbiddenException,
    InvalidRequestException,
    InvalidResponseException,
    TooManyRequestsException,
    UnauthorizedException,
)

try:
    from pywebasto import SimpleTimer, SmartTimer
    from pywebasto.enums import Outputs
except ImportError:

    @dataclass(slots=True)
    class SimpleTimer:  # type: ignore[no-redef]
        """Fallback timer model for local/test environments."""

        start: int
        duration: int
        repeat: int
        latitude: str | None = None
        longitude: str | None = None
        enabled: bool = True

    @dataclass(slots=True)
    class SmartTimer:  # type: ignore[no-redef]
        """Fallback smart timer model for local/test environments."""

        start: int
        max_duration: int
        comfort_level: int
        repeat: int
        latitude: str | None = None
        longitude: str | None = None
        enabled: bool = True

    @dataclass(frozen=True, slots=True)
    class _OutputLine:
        """Fallback enum-like output line wrapper."""

        value: str

    class Outputs:  # type: ignore[no-redef]
        """Fallback output constants for environments without timer build."""

        HEATER = _OutputLine("OUTH")
        VENTILATION = _OutputLine("OUTV")


from .const import (
    DOMAIN,
    SERVICE_CREATE_SMART_TIMER,
    SERVICE_CREATE_TIMER,
    SERVICE_DELETE_TIMER,
    SERVICE_UPDATE_SMART_TIMER,
    SERVICE_UPDATE_TIMER,
)

LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID = "device_id"
ATTR_TIMER_INDEX = "timer_index"
ATTR_START = "start"
ATTR_DURATION = "duration"
ATTR_START_TIME = "start_time"
ATTR_DURATION_MINUTES = "duration_minutes"
ATTR_REPEAT = "repeat"
ATTR_REPEAT_DAYS = "repeat_days"
ATTR_ENABLED = "enabled"
ATTR_LOCATION = "location"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_CLEAR_LOCATION = "clear_location"
ATTR_LINE = "line"
ATTR_TYPE = "type"
ATTR_DEPARTURE_TIME = "departure_time"
ATTR_MAX_DURATION_MINUTES = "max_duration_minutes"
ATTR_COMFORT_LEVEL = "comfort_level"
TIMER_TYPE_SIMPLE = "simple"
TIMER_TYPE_SMART = "smart"
TIMER_TYPES = (TIMER_TYPE_SIMPLE, TIMER_TYPE_SMART)
LINE_HEATER = "heater"
LINE_VENTILATION = "ventilation"
LINE_HEATER_LEGACY = "OUTH"
LINE_VENTILATION_LEGACY = "OUTV"
VALID_TIMER_LINES = (
    LINE_HEATER,
    LINE_VENTILATION,
    LINE_HEATER_LEGACY,
    LINE_VENTILATION_LEGACY,
)
WEEKDAY_TO_MASK = {
    "monday": 1,
    "tuesday": 2,
    "wednesday": 4,
    "thursday": 8,
    "friday": 16,
    "saturday": 32,
    "sunday": 64,
}

MAX_DURATION_MINUTES = 1440
MAX_REPEAT_MASK = 127  # Monday..Sunday bits
# Smart timers: the heater starts by itself before the departure time, for at
# most `max_duration`. Observed values are 30 minutes (app) and 50 minutes
# (web dump); 120 minutes matches the longest heater run time setting seen
# (7200 s) and keeps the start within a sensible window before departure.
MAX_SMART_DURATION_MINUTES = 120
# Only comfort level 5 has been observed. The real range is unknown, so a
# small range around it is accepted; this is not verified against a device.
MIN_COMFORT_LEVEL = 1
MAX_COMFORT_LEVEL = 10
DEFAULT_COMFORT_LEVEL = 5


def _geo_string(validator: Any) -> Any:
    """Validate a legacy latitude/longitude string field.

    Empty and "none"-like values pass through so they can clear a location;
    anything else must be a valid coordinate and is returned as a string.
    """

    def _validate(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {
            "",
            "null",
            "none",
            "nan",
        }:
            return value
        return str(validator(value))

    return _validate


_DURATION_MINUTES = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_DURATION_MINUTES))
_MAX_DURATION_MINUTES = vol.All(
    vol.Coerce(int), vol.Range(min=1, max=MAX_SMART_DURATION_MINUTES)
)
_COMFORT_LEVEL = vol.All(
    vol.Coerce(int), vol.Range(min=MIN_COMFORT_LEVEL, max=MAX_COMFORT_LEVEL)
)
_REPEAT = vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_REPEAT_MASK))
_LOCATION_SCHEMA = vol.Schema(
    {
        vol.Required("latitude"): cv.latitude,
        vol.Required("longitude"): cv.longitude,
        vol.Optional("radius"): vol.Coerce(float),
    }
)

_BASE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})
_CREATE_TIMER_SCHEMA = _BASE_SCHEMA.extend(
    {
        vol.Optional(ATTR_LINE): vol.In(VALID_TIMER_LINES),
        vol.Required(ATTR_START_TIME): cv.string,
        vol.Required(ATTR_DURATION_MINUTES): _DURATION_MINUTES,
        vol.Optional(ATTR_REPEAT_DAYS, default=[]): [vol.In(tuple(WEEKDAY_TO_MASK))],
        vol.Optional(ATTR_REPEAT): _REPEAT,
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        vol.Optional(ATTR_LOCATION): _LOCATION_SCHEMA,
        vol.Optional(ATTR_LATITUDE): _geo_string(cv.latitude),
        vol.Optional(ATTR_LONGITUDE): _geo_string(cv.longitude),
    }
)
_UPDATE_TIMER_SCHEMA = _BASE_SCHEMA.extend(
    {
        vol.Optional(ATTR_LINE, default=LINE_HEATER): vol.In(VALID_TIMER_LINES),
        vol.Required(ATTR_TIMER_INDEX): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(ATTR_START_TIME): cv.string,
        vol.Optional(ATTR_DURATION_MINUTES): _DURATION_MINUTES,
        vol.Optional(ATTR_REPEAT_DAYS): [vol.In(tuple(WEEKDAY_TO_MASK))],
        vol.Optional(ATTR_REPEAT): _REPEAT,
        vol.Optional(ATTR_ENABLED): cv.boolean,
        vol.Optional(ATTR_CLEAR_LOCATION): cv.boolean,
        vol.Optional(ATTR_LOCATION): _LOCATION_SCHEMA,
        vol.Optional(ATTR_LATITUDE): _geo_string(cv.latitude),
        vol.Optional(ATTR_LONGITUDE): _geo_string(cv.longitude),
    }
)
_CREATE_SMART_TIMER_SCHEMA = _BASE_SCHEMA.extend(
    {
        vol.Optional(ATTR_LINE): vol.In(VALID_TIMER_LINES),
        vol.Required(ATTR_DEPARTURE_TIME): cv.string,
        vol.Required(ATTR_MAX_DURATION_MINUTES): _MAX_DURATION_MINUTES,
        vol.Optional(ATTR_COMFORT_LEVEL, default=DEFAULT_COMFORT_LEVEL): (
            _COMFORT_LEVEL
        ),
        vol.Optional(ATTR_REPEAT_DAYS, default=[]): [vol.In(tuple(WEEKDAY_TO_MASK))],
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        vol.Optional(ATTR_LOCATION): _LOCATION_SCHEMA,
    }
)
_UPDATE_SMART_TIMER_SCHEMA = _BASE_SCHEMA.extend(
    {
        vol.Optional(ATTR_LINE, default=LINE_HEATER): vol.In(VALID_TIMER_LINES),
        vol.Required(ATTR_TIMER_INDEX): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(ATTR_DEPARTURE_TIME): cv.string,
        vol.Optional(ATTR_MAX_DURATION_MINUTES): _MAX_DURATION_MINUTES,
        vol.Optional(ATTR_COMFORT_LEVEL): _COMFORT_LEVEL,
        vol.Optional(ATTR_REPEAT_DAYS): [vol.In(tuple(WEEKDAY_TO_MASK))],
        vol.Optional(ATTR_ENABLED): cv.boolean,
        vol.Optional(ATTR_CLEAR_LOCATION): cv.boolean,
        vol.Optional(ATTR_LOCATION): _LOCATION_SCHEMA,
    }
)
_DELETE_TIMER_SCHEMA = _BASE_SCHEMA.extend(
    {
        vol.Required(ATTR_TIMER_INDEX): vol.All(vol.Coerce(int), vol.Range(min=0)),
        # Without a line the active output is used, as before.
        vol.Optional(ATTR_LINE): vol.In(VALID_TIMER_LINES),
        vol.Optional(ATTR_TYPE, default=TIMER_TYPE_SIMPLE): vol.In(TIMER_TYPES),
    }
)


def _validation_error(
    translation_key: str, placeholders: dict[str, str] | None = None
) -> ServiceValidationError:
    """Return a translated error for invalid action input."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders=placeholders,
    )


def _loaded_coordinators(hass: HomeAssistant) -> list[Any]:
    """Return the coordinators of the loaded config entries."""
    return [
        entry.runtime_data.coordinator
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        and getattr(entry, "runtime_data", None) is not None
    ]


def _coordinator_and_device(hass: HomeAssistant, device_id: str) -> tuple[Any, Any]:
    """Resolve coordinator + device from HA device id or Webasto API device id.

    Raises ServiceValidationError when the device is unknown, when its config
    entry is not loaded, or when the account no longer reports it.
    """
    # Backwards compatibility for callers that still pass API device id directly.
    for coordinator in _loaded_coordinators(hass):
        for cloud_device in coordinator.cloud.devices.values():
            if str(cloud_device.device_id) == str(device_id):
                return coordinator, cloud_device

    # Preferred path: device selector value (HA device registry id).
    ha_device = dr.async_get(hass).async_get(str(device_id))
    if ha_device is None:
        raise _validation_error("device_not_found", {"device_id": str(device_id)})

    entries = [
        entry
        for entry_id in ha_device.config_entries
        if (entry := hass.config_entries.async_get_entry(entry_id)) is not None
        and entry.domain == DOMAIN
    ]
    if not entries:
        raise _validation_error("device_not_found", {"device_id": str(device_id)})

    loaded_entries = [
        entry for entry in entries if entry.state is ConfigEntryState.LOADED
    ]
    if not loaded_entries:
        raise _validation_error("entry_not_loaded", {"entry_title": entries[0].title})

    internal_device_ids = {
        identifier for domain, identifier in ha_device.identifiers if domain == DOMAIN
    }
    for entry in loaded_entries:
        coordinator = entry.runtime_data.coordinator
        for internal_id, cloud_device in coordinator.cloud.devices.items():
            if str(internal_id) in internal_device_ids:
                return coordinator, cloud_device

    raise _validation_error(
        "device_not_reported",
        {"device_name": ha_device.name_by_user or ha_device.name or str(device_id)},
    )


def _ensure_timer_api_support(coordinator: Any) -> None:
    """Validate that installed pywebasto provides timer APIs."""
    if not hasattr(coordinator.cloud, "get_timers") or not hasattr(
        coordinator.cloud, "save_timers"
    ):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="timers_not_supported",
        )


def _ensure_all_timers_api_support(coordinator: Any) -> None:
    """Validate that installed pywebasto can read and save every timer type."""
    if not hasattr(coordinator.cloud, "get_all_timers") or not hasattr(
        coordinator.cloud, "save_all_timers"
    ):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="timers_not_supported",
        )


def _output_for_line(line: str) -> Any:
    """Map API line id to pywebasto Outputs value."""
    normalized = line.strip().lower()
    if normalized in (LINE_VENTILATION, LINE_VENTILATION_LEGACY.lower()):
        return Outputs.VENTILATION
    return Outputs.HEATER


def _active_output_for_device(device: Any) -> Any:
    """Resolve active output line from current device mode."""
    return (
        Outputs.VENTILATION
        if bool(getattr(device, "is_ventilation", False))
        else Outputs.HEATER
    )


def _raise_timer_index_error(timer_index: int, timer_count: int) -> None:
    """Raise a user-friendly out-of-range error for timer index."""
    if timer_count <= 0:
        raise _validation_error("no_timers")

    raise _validation_error(
        "invalid_timer_index",
        {
            "timer_index": str(timer_index),
            "max_index": str(timer_count - 1),
            "timer_count": str(timer_count),
        },
    )


def _normalize_geo_value(value: Any) -> str | None:
    """Normalize user-provided geo values."""
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.lower() in {"", "null", "none", "nan"}:
            return None
        return normalized
    return str(value)


def _invalid_clock_time(value: str, departure: bool) -> ServiceValidationError:
    """Return the error for a start or departure time that is not HH:MM."""
    if departure:
        return _validation_error("invalid_departure_time", {"departure_time": value})
    return _validation_error("invalid_start_time", {"start_time": value})


def _parse_clock_time(value: Any, *, departure: bool = False) -> time:
    """Parse selector value to clock time."""
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        raise _invalid_clock_time(str(value), departure)

    candidate = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(candidate, fmt).time()
        except ValueError:
            continue
    raise _invalid_clock_time(candidate, departure)


def _start_minutes_from_local_time(
    hass: HomeAssistant, value: Any, *, departure: bool = False
) -> int:
    """Convert local clock time to UTC minutes after midnight."""
    tz = dt_util.get_time_zone(hass.config.time_zone) or UTC
    clock = _parse_clock_time(value, departure=departure)
    now_local = datetime.now(tz)
    local_dt = datetime(
        now_local.year,
        now_local.month,
        now_local.day,
        clock.hour,
        clock.minute,
        tzinfo=tz,
    )
    utc_dt = local_dt.astimezone(UTC)
    return utc_dt.hour * 60 + utc_dt.minute


def _repeat_mask_from_days(days: Any) -> int:
    """Build repeat bitmask from weekday names."""
    if not isinstance(days, list):
        raise _validation_error("invalid_repeat_days")
    mask = 0
    for day in days:
        if not isinstance(day, str):
            raise _validation_error("invalid_repeat_days")
        key = day.strip().lower()
        if key not in WEEKDAY_TO_MASK:
            raise _validation_error("unsupported_weekday", {"day": day})
        mask |= WEEKDAY_TO_MASK[key]
    return mask


def _resolve_location_values(
    data: dict[str, Any],
    existing: Any | None,
    clear_location: bool,
) -> tuple[str | None, str | None]:
    """Resolve location values from selector/legacy fields/update flags."""
    location_data = data.get(ATTR_LOCATION)
    if location_data is not None and not isinstance(location_data, dict):
        raise _validation_error("invalid_location")

    latitude = (
        _normalize_geo_value(data.get(ATTR_LATITUDE))
        if ATTR_LATITUDE in data
        else _normalize_geo_value(getattr(existing, "latitude", None))
    )
    longitude = (
        _normalize_geo_value(data.get(ATTR_LONGITUDE))
        if ATTR_LONGITUDE in data
        else _normalize_geo_value(getattr(existing, "longitude", None))
    )

    if location_data is not None:
        if ATTR_LATITUDE in data or ATTR_LONGITUDE in data:
            raise _validation_error("location_with_coordinates")
        latitude = _normalize_geo_value(location_data.get("latitude"))
        longitude = _normalize_geo_value(location_data.get("longitude"))

    if clear_location and (ATTR_LATITUDE in data or ATTR_LONGITUDE in data):
        raise _validation_error("clear_location_with_coordinates")
    if clear_location and location_data is not None:
        raise _validation_error("clear_location_with_location")
    if clear_location:
        return None, None

    if (latitude is None) != (longitude is None):
        raise _validation_error("incomplete_coordinates")

    return latitude, longitude


def _coerce_timer(
    data: dict[str, Any],
    *,
    existing: Any | None = None,
    hass: HomeAssistant | None = None,
) -> SimpleTimer:
    """Build a SimpleTimer from service data, optionally patching an existing timer."""
    start = data.get(ATTR_START, getattr(existing, "start", None))
    if ATTR_START_TIME in data:
        if hass is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="start_time_conversion_unavailable",
            )
        start = _start_minutes_from_local_time(hass, data.get(ATTR_START_TIME))

    duration = data.get(ATTR_DURATION, getattr(existing, "duration", None))
    if ATTR_DURATION_MINUTES in data:
        duration = int(data[ATTR_DURATION_MINUTES]) * 60

    repeat = data.get(ATTR_REPEAT, getattr(existing, "repeat", None))
    if ATTR_REPEAT_DAYS in data:
        repeat = _repeat_mask_from_days(data[ATTR_REPEAT_DAYS])
    enabled = data.get(ATTR_ENABLED, getattr(existing, "enabled", True))
    clear_location = bool(data.get(ATTR_CLEAR_LOCATION, False))
    latitude, longitude = _resolve_location_values(data, existing, clear_location)

    if start is None or duration is None or repeat is None:
        raise _validation_error("missing_timer_values")

    return SimpleTimer(
        start=int(start),
        duration=int(duration),
        repeat=int(repeat),
        enabled=bool(enabled),
        latitude=latitude,
        longitude=longitude,
    )


def _coerce_smart_timer(
    data: dict[str, Any],
    *,
    existing: Any | None = None,
    hass: HomeAssistant | None = None,
) -> SmartTimer:
    """Build a SmartTimer from action data, optionally patching an existing one.

    The departure time is stored in `start` (minutes after midnight UTC), as
    the Webasto cloud returns it.
    """
    start = getattr(existing, "start", None)
    if ATTR_DEPARTURE_TIME in data:
        if hass is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="start_time_conversion_unavailable",
            )
        start = _start_minutes_from_local_time(
            hass, data[ATTR_DEPARTURE_TIME], departure=True
        )

    max_duration = getattr(existing, "max_duration", None)
    if ATTR_MAX_DURATION_MINUTES in data:
        max_duration = int(data[ATTR_MAX_DURATION_MINUTES]) * 60

    comfort_level = data.get(
        ATTR_COMFORT_LEVEL, getattr(existing, "comfort_level", None)
    )
    repeat = getattr(existing, "repeat", None)
    if ATTR_REPEAT_DAYS in data:
        repeat = _repeat_mask_from_days(data[ATTR_REPEAT_DAYS])
    enabled = data.get(ATTR_ENABLED, getattr(existing, "enabled", True))
    clear_location = bool(data.get(ATTR_CLEAR_LOCATION, False))
    latitude, longitude = _resolve_location_values(data, existing, clear_location)

    if None in (start, max_duration, comfort_level, repeat):
        raise _validation_error("missing_smart_timer_values")

    return SmartTimer(
        start=int(start),
        max_duration=int(max_duration),
        comfort_level=int(comfort_level),
        repeat=int(repeat),
        enabled=bool(enabled),
        latitude=latitude,
        longitude=longitude,
    )


def _smart_positions(timers: list[Any]) -> list[int]:
    """Return the list positions of the smart timers in a full timer list."""
    return [
        position
        for position, timer in enumerate(timers)
        if isinstance(timer, SmartTimer)
    ]


async def async_create_timer(
    coordinator: Any,
    device: Any,
    timer: SimpleTimer,
    line: str | None = None,
) -> None:
    """Create timer by appending to current timer list."""

    async def _operation() -> None:
        output = (
            _output_for_line(line)
            if line is not None
            else _active_output_for_device(device)
        )
        timers = await coordinator.cloud.get_timers(device=device, line=output)
        timers.append(timer)
        await coordinator.cloud.save_timers(device=device, timers=timers, line=output)

    await coordinator.async_execute_cloud_call(_operation)
    coordinator.async_update_listeners()


async def async_update_timer(
    coordinator: Any,
    device: Any,
    timer_index: int,
    timer_data: dict[str, Any],
    line: str | None = None,
    hass: HomeAssistant | None = None,
) -> None:
    """Update timer at index and persist full timer list."""

    async def _operation() -> None:
        selected_index = timer_index
        total_timers = 0

        if line is not None:
            output = _output_for_line(line)
            timers = await coordinator.cloud.get_timers(device=device, line=output)
            total_timers = len(timers)
        else:
            heater_timers = await coordinator.cloud.get_timers(
                device=device, line=Outputs.HEATER
            )
            if timer_index < len(heater_timers):
                output = Outputs.HEATER
                timers = heater_timers
            else:
                vent_timers = await coordinator.cloud.get_timers(
                    device=device, line=Outputs.VENTILATION
                )
                selected_index = timer_index - len(heater_timers)
                output = Outputs.VENTILATION
                timers = vent_timers
                total_timers = len(heater_timers) + len(vent_timers)

            if total_timers == 0:
                total_timers = len(timers)

        if selected_index >= len(timers):
            _raise_timer_index_error(timer_index, total_timers)

        timers[selected_index] = _coerce_timer(
            timer_data,
            existing=timers[selected_index],
            hass=hass,
        )
        await coordinator.cloud.save_timers(device=device, timers=timers, line=output)

    await coordinator.async_execute_cloud_call(_operation)
    coordinator.async_update_listeners()


async def async_create_smart_timer(
    coordinator: Any,
    device: Any,
    timer: SmartTimer,
    line: str | None = None,
) -> None:
    """Create a smart timer; every other timer on the line is kept as-is."""

    async def _operation() -> None:
        output = (
            _output_for_line(line)
            if line is not None
            else _active_output_for_device(device)
        )
        timers = await coordinator.cloud.get_all_timers(device=device, line=output)
        timers.append(timer)
        await coordinator.cloud.save_all_timers(
            device=device, timers=timers, line=output
        )

    await coordinator.async_execute_cloud_call(_operation)
    coordinator.async_update_listeners()


async def async_update_smart_timer(
    coordinator: Any,
    device: Any,
    timer_index: int,
    timer_data: dict[str, Any],
    line: str = LINE_HEATER,
    hass: HomeAssistant | None = None,
) -> None:
    """Update the smart timer at `timer_index` (counting smart timers only)."""

    async def _operation() -> None:
        output = _output_for_line(line)
        timers = await coordinator.cloud.get_all_timers(device=device, line=output)
        positions = _smart_positions(timers)
        if timer_index >= len(positions):
            _raise_timer_index_error(timer_index, len(positions))
        position = positions[timer_index]
        timers[position] = _coerce_smart_timer(
            timer_data, existing=timers[position], hass=hass
        )
        await coordinator.cloud.save_all_timers(
            device=device, timers=timers, line=output
        )

    await coordinator.async_execute_cloud_call(_operation)
    coordinator.async_update_listeners()


async def async_delete_timer(
    coordinator: Any,
    device: Any,
    timer_index: int,
    line: str | None = None,
    timer_type: str = TIMER_TYPE_SIMPLE,
) -> None:
    """Delete a timer by index and persist the full timer list.

    The index counts the timers of `timer_type` on the line only, so the
    index of a simple timer does not change when smart timers exist.
    """

    async def _operation() -> None:
        output = (
            _output_for_line(line)
            if line is not None
            else _active_output_for_device(device)
        )
        if timer_type == TIMER_TYPE_SMART:
            all_timers = await coordinator.cloud.get_all_timers(
                device=device, line=output
            )
            positions = _smart_positions(all_timers)
            if timer_index >= len(positions):
                _raise_timer_index_error(timer_index, len(positions))
            del all_timers[positions[timer_index]]
            await coordinator.cloud.save_all_timers(
                device=device, timers=all_timers, line=output
            )
            return

        timers = await coordinator.cloud.get_timers(device=device, line=output)
        if timer_index >= len(timers):
            _raise_timer_index_error(timer_index, len(timers))
        del timers[timer_index]
        await coordinator.cloud.save_timers(device=device, timers=timers, line=output)

    await coordinator.async_execute_cloud_call(_operation)
    coordinator.async_update_listeners()


_CLOUD_ERRORS = (
    InvalidRequestException,
    UnauthorizedException,
    ForbiddenException,
    InvalidResponseException,
    TooManyRequestsException,
)


def _invalid_timer_error(err: ValueError) -> ServiceValidationError:
    """Return a translated error for a timer the library rejected."""
    return _validation_error("invalid_timer", {"error": str(err)})


def _cloud_error(translation_key: str, err: Exception) -> HomeAssistantError:
    """Return a translated error for a failed cloud request.

    The library message can hold response details, so it is logged at debug
    level and kept out of the message shown to the user.
    """
    LOGGER.debug("Webasto timer request failed (%s): %s", type(err).__name__, err)
    if isinstance(err, TooManyRequestsException):
        translation_key = "rate_limited"
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
    )


async def _async_handle_create_timer(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle create_timer service."""
    coordinator, device = _coordinator_and_device(hass, call.data[ATTR_DEVICE_ID])
    _ensure_timer_api_support(coordinator)
    timer = _coerce_timer(dict(call.data), hass=hass)
    line = (
        str(call.data[ATTR_LINE])
        if ATTR_LINE in call.data and call.data[ATTR_LINE] is not None
        else None
    )

    try:
        await async_create_timer(coordinator, device, timer, line)
    except ValueError as err:
        raise _invalid_timer_error(err) from err
    except _CLOUD_ERRORS as err:
        raise _cloud_error("create_timer_failed", err) from err


async def _async_handle_update_timer(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle update_timer service."""
    coordinator, device = _coordinator_and_device(hass, call.data[ATTR_DEVICE_ID])
    _ensure_timer_api_support(coordinator)
    timer_index = int(call.data[ATTR_TIMER_INDEX])
    line = (
        str(call.data[ATTR_LINE])
        if ATTR_LINE in call.data and call.data[ATTR_LINE] is not None
        else None
    )

    try:
        await async_update_timer(
            coordinator,
            device,
            timer_index,
            dict(call.data),
            line,
            hass=hass,
        )
    except ValueError as err:
        raise _invalid_timer_error(err) from err
    except _CLOUD_ERRORS as err:
        raise _cloud_error("update_timer_failed", err) from err


async def _async_handle_create_smart_timer(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle create_smart_timer service."""
    coordinator, device = _coordinator_and_device(hass, call.data[ATTR_DEVICE_ID])
    _ensure_all_timers_api_support(coordinator)
    timer = _coerce_smart_timer(dict(call.data), hass=hass)
    line = call.data.get(ATTR_LINE)

    try:
        await async_create_smart_timer(
            coordinator, device, timer, None if line is None else str(line)
        )
    except ValueError as err:
        raise _invalid_timer_error(err) from err
    except _CLOUD_ERRORS as err:
        raise _cloud_error("create_timer_failed", err) from err


async def _async_handle_update_smart_timer(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle update_smart_timer service."""
    coordinator, device = _coordinator_and_device(hass, call.data[ATTR_DEVICE_ID])
    _ensure_all_timers_api_support(coordinator)

    try:
        await async_update_smart_timer(
            coordinator,
            device,
            int(call.data[ATTR_TIMER_INDEX]),
            dict(call.data),
            str(call.data.get(ATTR_LINE) or LINE_HEATER),
            hass=hass,
        )
    except ValueError as err:
        raise _invalid_timer_error(err) from err
    except _CLOUD_ERRORS as err:
        raise _cloud_error("update_timer_failed", err) from err


async def _async_handle_delete_timer(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle delete_timer service."""
    coordinator, device = _coordinator_and_device(hass, call.data[ATTR_DEVICE_ID])
    timer_type = str(call.data.get(ATTR_TYPE, TIMER_TYPE_SIMPLE))
    if timer_type == TIMER_TYPE_SMART:
        _ensure_all_timers_api_support(coordinator)
    else:
        _ensure_timer_api_support(coordinator)
    timer_index = int(call.data[ATTR_TIMER_INDEX])
    line = call.data.get(ATTR_LINE)

    try:
        await async_delete_timer(
            coordinator,
            device,
            timer_index,
            None if line is None else str(line),
            timer_type,
        )
    except ValueError as err:
        raise _invalid_timer_error(err) from err
    except _CLOUD_ERRORS as err:
        raise _cloud_error("delete_timer_failed", err) from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration actions once, from async_setup."""

    async def _handle_create(call: ServiceCall) -> None:
        await _async_handle_create_timer(hass, call)

    async def _handle_update(call: ServiceCall) -> None:
        await _async_handle_update_timer(hass, call)

    async def _handle_delete(call: ServiceCall) -> None:
        await _async_handle_delete_timer(hass, call)

    async def _handle_create_smart(call: ServiceCall) -> None:
        await _async_handle_create_smart_timer(hass, call)

    async def _handle_update_smart(call: ServiceCall) -> None:
        await _async_handle_update_smart_timer(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_TIMER,
        _handle_create,
        schema=_CREATE_TIMER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_TIMER,
        _handle_update,
        schema=_UPDATE_TIMER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_TIMER,
        _handle_delete,
        schema=_DELETE_TIMER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_SMART_TIMER,
        _handle_create_smart,
        schema=_CREATE_SMART_TIMER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_SMART_TIMER,
        _handle_update_smart,
        schema=_UPDATE_SMART_TIMER_SCHEMA,
    )
