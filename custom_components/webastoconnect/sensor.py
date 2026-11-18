"""Sensors for Webasto Connect."""

from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from . import WebastoConfigEntry
from .api import WebastoConnectUpdateCoordinator
from .base import WebastoBaseEntity, WebastoConnectSensorEntityDescription
from .subscription import subscription_days_left

LOGGER = logging.getLogger(__name__)
# Read-only entities updated by the coordinator.
PARALLEL_UPDATES = 0
MAIN_OUTPUT_LINES = {"OUTH", "OUTV"}
TIMER_LINES = {"OUTH", "OUTV"}
# `type` values of the backend `nextEvent`, mapped to the timer types used in
# this integration. Other values are passed through as-is.
NEXT_EVENT_TYPES = {"normal": "simple", "smart": "smart"}
# Name of the main output in the end-time sensor name when the cloud has none.
# Not translated: it keeps the entity ID of a new install at *_output_ends.
MAIN_OUTPUT_FALLBACK_NAME = "Output"
WEEKDAY_BITMASK = [1, 2, 4, 8, 16, 32, 64]  # Monday..Sunday
# Values of the `def_output` and `webasto_emul_mode` settings, as documented in
# the pywebasto protocol notes.
DEFAULT_OUTPUT_OPTIONS = [
    "automatic",
    "output1",
    "OUTA",
    "webasto_heater",
    "webasto_ventilation",
]
EMULATION_MODE_OPTIONS = [
    "wb_auto_detect",
    "thermoconnect",
    "telestart",
    "telestart_special",
    "timer",
]


def _subscription_expiration_date(webasto: Any) -> date | None:
    """Return the subscription expiration date (UTC), if known."""
    expiration = getattr(webasto, "subscription_expiration", None)
    if isinstance(expiration, datetime):
        return expiration.date()
    return None


def _enum_value(key: str, value: Any, options: list[str]) -> str | None:
    """Return the value when it is a known option, else None.

    An enum sensor must not report a value outside its options, so an
    unknown value from the cloud is logged and reported as unknown.
    """
    if value is None:
        return None
    if value not in options:
        LOGGER.debug("Ignoring unknown %s value '%s'", key, value)
        return None
    return value


def _main_output_end_time(webasto) -> datetime | None:
    """Return the UTC end timestamp for the active main output."""
    end_time = getattr(webasto, "output_main_end_time", None)
    if isinstance(end_time, datetime):
        return end_time if end_time.tzinfo is not None else end_time.replace(tzinfo=UTC)

    last_data = getattr(webasto, "last_data", None)
    if not isinstance(last_data, dict):
        return None

    outputs = last_data.get("outputs")
    if not isinstance(outputs, list):
        return None

    for output in outputs:
        if not isinstance(output, dict):
            continue
        if output.get("line") not in MAIN_OUTPUT_LINES:
            continue
        if output.get("state") != "ON":
            continue

        ontime = output.get("ontime")
        if isinstance(ontime, int | float) and ontime > 0:
            return datetime.fromtimestamp(ontime, UTC)

    return None


def _main_output_end_placeholders(webasto) -> dict[str, str]:
    """Return the name placeholders of the main output end-time sensor.

    The translated name is "{output_name} ends"; the output name is the one
    set in the Webasto app (it follows the heating or ventilation mode).
    """
    output_name = getattr(webasto, "output_main_name", None)
    if isinstance(output_name, str) and output_name.strip():
        return {"output_name": output_name}
    return {"output_name": MAIN_OUTPUT_FALLBACK_NAME}


TIMER_TYPES = ("simple", "smart")


def _extract_timers(webasto: Any) -> list[dict[str, Any]]:
    """Extract `simple` and `smart` timers (heater + ventilation) from API data.

    Timers keep the order of the API data. Timers of other types are left
    out; they are not counted in any timer index.
    """
    last_data = getattr(webasto, "last_data", None)
    if not isinstance(last_data, dict):
        return []

    timers: list[dict[str, Any]] = []
    for section in ("outputs", "disabled_outputs"):
        outputs = last_data.get(section)
        if not isinstance(outputs, list):
            continue
        for output in outputs:
            if not isinstance(output, dict):
                continue
            line = output.get("line")
            if line not in TIMER_LINES:
                continue
            output_timers = output.get("timers")
            if not isinstance(output_timers, list):
                continue
            for timer in output_timers:
                if isinstance(timer, dict) and timer.get("type") in TIMER_TYPES:
                    timers.append(
                        {
                            **timer,
                            "line": line,
                        }
                    )
    return timers


def _next_timer_occurrence_utc(
    *,
    start: int,
    repeat: int,
    now_utc: datetime,
) -> datetime | None:
    """Calculate next UTC occurrence for a timer."""
    if start <= 0 or start >= 24 * 60:
        return None

    hour = start // 60
    minute = start % 60

    if repeat == 0:
        candidate = now_utc.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now_utc:
            candidate += timedelta(days=1)
        return candidate

    for day_offset in range(0, 8):
        candidate_date = now_utc.date() + timedelta(days=day_offset)
        weekday = candidate_date.weekday()
        if repeat & WEEKDAY_BITMASK[weekday] == 0:
            continue

        candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            hour,
            minute,
            tzinfo=UTC,
        )
        if candidate > now_utc:
            return candidate

    return None


def _timer_start_hhmm(start: int) -> str:
    """Format timer start (minutes after midnight) as HH:MM."""
    hour = start // 60
    minute = start % 60
    return f"{hour:02d}:{minute:02d}"


def _line_label(line: str | None) -> str | None:
    """Return user-friendly timer line label."""
    if line == "OUTH":
        return "Heater"
    if line == "OUTV":
        return "Ventilation"
    return line


def _timer_values(timer: dict[str, Any]) -> dict[str, Any]:
    """Return the type-specific attribute values of a timer.

    Raises TypeError or ValueError for a malformed timer.
    """
    start = int(timer.get("start") or 0)
    if timer.get("type") == "smart":
        # `start` of a smart timer is the departure time; the heater starts
        # by itself before it, at most `max_duration` seconds earlier.
        return {
            "departure": start,
            "departure_hhmm_utc": _timer_start_hhmm(start) if start > 0 else None,
            "max_duration": int(timer.get("maxDuration") or 0),
            "comfort_level": int(timer.get("comfortLevel") or 0),
        }
    return {
        "start": start,
        "start_hhmm_utc": _timer_start_hhmm(start) if start > 0 else None,
        "duration": int(timer.get("duration") or 0),
    }


def _next_timer_sensor_payload(
    webasto: Any,
    *,
    now_utc: datetime | None = None,
) -> tuple[datetime | None, dict[str, Any]]:
    """Build state + attributes for next-enabled-timer sensor.

    `index` of a timer is its position among the timers of the same type on
    the same line: the `timer_index` the actions use together with `line`
    (and `type` for delete_timer). Simple timers keep the indexes they had
    before smart timers were shown. For a smart timer `next_run_utc` is the
    departure time; the heater starts earlier, up to `max_duration` before.
    """
    now = now_utc or datetime.now(UTC)
    timers = _extract_timers(webasto)

    timer_items: list[dict[str, Any]] = []
    next_index: int | None = None
    next_occurrence: datetime | None = None
    next_timer: dict[str, Any] | None = None
    counters: dict[tuple[Any, Any], int] = {}

    for timer in timers:
        timer_type = timer.get("type")
        key = (timer.get("line"), timer_type)
        index = counters.get(key, 0)
        counters[key] = index + 1
        try:
            values = _timer_values(timer)
            repeat = int(timer.get("repeat") or 0)
        except TypeError, ValueError:
            # Keep the index aligned with the timer services, but do not let
            # one malformed timer from the cloud break the whole sensor.
            LOGGER.debug("Skipping malformed %s timer at index %s", timer_type, index)
            continue
        start = values.get("start", values.get("departure", 0))
        enabled = bool(timer.get("enabled", False))
        location = timer.get("location")
        # Only expose whether a location is set: attributes end up in the
        # recorder and are visible to every user, and the coordinates are
        # usually home or work. Services keep the stored location as-is.
        has_location = (
            isinstance(location, dict)
            and location.get("lat") is not None
            and location.get("lon") is not None
        )

        occurrence = None
        if enabled and start > 0:
            occurrence = _next_timer_occurrence_utc(
                start=start,
                repeat=repeat,
                now_utc=now,
            )

        item = {
            "index": index,
            "type": timer_type,
            "line": _line_label(timer.get("line")),
            "line_code": timer.get("line"),
            **values,
            "repeat": repeat,
            "enabled": enabled,
            "has_location": has_location,
            "next_run_utc": occurrence.isoformat() if occurrence else None,
        }
        timer_items.append(item)

        if not enabled or occurrence is None:
            continue
        if next_occurrence is None or occurrence < next_occurrence:
            next_occurrence = occurrence
            next_index = index
            next_timer = item

    return next_occurrence, {
        "next_timer_index": next_index,
        "next_timer_type": None if next_timer is None else next_timer["type"],
        "next_timer": next_timer,
        "timers": timer_items,
    }


def _int_or_none(value: Any) -> int | None:
    """Return an int value (booleans excluded), otherwise None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _str_or_none(value: Any) -> str | None:
    """Return a str value, otherwise None."""
    return value if isinstance(value, str) else None


def _next_event(webasto: Any) -> dict[str, Any] | None:
    """Return the backend's next timer event (`nextEvent`), or None.

    Uses `WebastoDevice.next_event` when the library has it and otherwise
    parses `last_data["nextEvent"]`, so this works with pywebasto-ng 3.1.1.
    `time_utc_minutes` is the timer start in minutes after midnight UTC.
    The meaning of `day` is unknown: it is kept raw and no date is derived
    from it.
    """
    event = getattr(webasto, "next_event", None)
    if isinstance(getattr(event, "raw", None), dict):
        raw = {
            "line": getattr(event, "line", None),
            "type": getattr(event, "type", None),
            "time": getattr(event, "time_utc_minutes", None),
            "day": getattr(event, "day", None),
        }
    else:
        last_data = getattr(webasto, "last_data", None)
        raw = last_data.get("nextEvent") if isinstance(last_data, dict) else None
        if not isinstance(raw, dict) or not raw:
            return None

    time_utc_minutes = _int_or_none(raw.get("time"))
    if time_utc_minutes is not None and not 0 <= time_utc_minutes < 24 * 60:
        time_utc_minutes = None
    return {
        "line": _str_or_none(raw.get("line")),
        "type": _str_or_none(raw.get("type")),
        "time_utc_minutes": time_utc_minutes,
        "day": _int_or_none(raw.get("day")),
    }


def _next_event_local_time(
    webasto: Any, *, now_utc: datetime | None = None
) -> str | None:
    """Return the next event's time of day as HH:MM in Home Assistant's timezone.

    The date of the event is unknown, so the UTC time is placed on today's
    UTC date and converted with today's offset. Around a DST change the
    shown time can be off by the DST difference.
    """
    event = _next_event(webasto)
    if event is None or event["time_utc_minutes"] is None:
        return None
    minutes = event["time_utc_minutes"]
    today = (now_utc or dt_util.utcnow()).date()
    event_utc = datetime(
        today.year, today.month, today.day, minutes // 60, minutes % 60, tzinfo=UTC
    )
    return dt_util.as_local(event_utc).strftime("%H:%M")


def _next_event_attributes(webasto: Any) -> dict[str, Any] | None:
    """Return the attributes of the next event sensor, or None."""
    event = _next_event(webasto)
    if event is None:
        return None
    event_type = event["type"]
    minutes = event["time_utc_minutes"]
    return {
        "line": _line_label(event["line"]),
        "type": NEXT_EVENT_TYPES.get(event_type, event_type),
        "time_utc": None if minutes is None else _timer_start_hhmm(minutes),
        "day_raw": event["day"],
    }


SENSORS = [
    WebastoConnectSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        entity_category=None,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
        value_fn=lambda webasto: webasto.temperature,
        unit_fn=lambda webasto: webasto.temperature_unit,
        suggested_display_precision=0,
    ),
    WebastoConnectSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        entity_category=None,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_fn=lambda webasto: webasto.voltage,
        suggested_display_precision=1,
    ),
    WebastoConnectSensorEntityDescription(
        key="subscription_expiration",
        translation_key="subscription_expiration",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=SensorDeviceClass.DATE,
        entity_registry_enabled_default=False,
        value_fn=_subscription_expiration_date,
    ),
    # Same count as the repair issue: 0 on the last day, negative once
    # expired, unknown without an expiration date. It changes at local
    # midnight and is picked up by the next coordinator update.
    WebastoConnectSensorEntityDescription(
        key="subscription_days_left",
        translation_key="subscription_days_left",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=0,
        value_fn=lambda webasto: subscription_days_left(
            getattr(webasto, "subscription_expiration", None)
        ),
    ),
    WebastoConnectSensorEntityDescription(
        key="main_output_end_time",
        translation_key="main_output_end_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=_main_output_end_time,
        placeholders_fn=_main_output_end_placeholders,
    ),
    WebastoConnectSensorEntityDescription(
        key="next_enabled_timer",
        translation_key="next_enabled_timer",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=lambda webasto: _next_timer_sensor_payload(webasto)[0],
    ),
    # Webasto's own next timer event. The state is a time of day only: the
    # backend's `day` field is not understood, so no date is shown.
    WebastoConnectSensorEntityDescription(
        key="next_event",
        translation_key="next_event",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=None,
        entity_registry_enabled_default=False,
        value_fn=_next_event_local_time,
        attributes_fn=_next_event_attributes,
    ),
    WebastoConnectSensorEntityDescription(
        key="last_seen",
        translation_key="last_seen",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_registry_enabled_default=False,
        value_fn=lambda webasto: getattr(webasto, "last_seen_seconds", None),
    ),
    WebastoConnectSensorEntityDescription(
        key="geofence_radius",
        translation_key="geofence_radius",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        entity_registry_enabled_default=False,
        value_fn=lambda webasto: getattr(webasto, "geofence_radius", None),
        suggested_display_precision=1,
    ),
    WebastoConnectSensorEntityDescription(
        key="alarm_state",
        translation_key="alarm_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=None,
        entity_registry_enabled_default=False,
        value_fn=lambda webasto: getattr(webasto, "alarm_state", None),
    ),
    WebastoConnectSensorEntityDescription(
        key="subscription_status",
        translation_key="subscription_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=None,
        entity_registry_enabled_default=False,
        value_fn=lambda webasto: getattr(webasto, "subscription_status", None),
    ),
    WebastoConnectSensorEntityDescription(
        key="default_output",
        translation_key="default_output",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        options=DEFAULT_OUTPUT_OPTIONS,
        entity_registry_enabled_default=False,
        value_fn=lambda webasto: _enum_value(
            "default_output",
            getattr(webasto, "default_output", None),
            DEFAULT_OUTPUT_OPTIONS,
        ),
    ),
    WebastoConnectSensorEntityDescription(
        key="signal_lost_shutdown_minutes",
        translation_key="signal_lost_shutdown_minutes",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_registry_enabled_default=False,
        value_fn=lambda webasto: getattr(webasto, "signal_lost_shutdown_minutes", None),
    ),
    WebastoConnectSensorEntityDescription(
        key="emulation_mode",
        translation_key="emulation_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        options=EMULATION_MODE_OPTIONS,
        entity_registry_enabled_default=False,
        value_fn=lambda webasto: _enum_value(
            "emulation_mode",
            getattr(webasto, "emulation_mode", None),
            EMULATION_MODE_OPTIONS,
        ),
    ),
    WebastoConnectSensorEntityDescription(
        key="software_external_version",
        translation_key="software_external_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
        device_class=None,
        entity_registry_enabled_default=False,
        value_fn=lambda webasto: getattr(webasto, "software_external_version", None),
    ),
]


async def async_setup_entry(hass, entry: WebastoConfigEntry, async_add_devices):
    """Set up sensors."""
    sensors = []

    coordinator = entry.runtime_data.coordinator

    for id, device in coordinator.cloud.devices.items():
        LOGGER.debug("Setting up sensors for device: %s", device.name)
        for s in SENSORS:
            entity = WebastoConnectSensor(id, s, coordinator)
            LOGGER.debug("Adding sensor '%s'", s.key)
            sensors.append(entity)

    async_add_devices(sensors)


class WebastoConnectSensor(WebastoBaseEntity, SensorEntity):
    """Representation of a Webasto Connect Sensor."""

    def __init__(
        self,
        device_id: int,
        description: WebastoConnectSensorEntityDescription,
        coordinator: WebastoConnectUpdateCoordinator,
    ) -> None:
        """Initialize a Webasto Connect Sensor."""
        super().__init__(device_id, coordinator, description)

        if description.placeholders_fn is not None:
            self._attr_translation_placeholders = description.placeholders_fn(
                self._cloud.devices[self._device_id]
            )
        self._attr_native_value = self.entity_description.value_fn(  # type: ignore
            self._cloud.devices[self._device_id]
        )
        if self.entity_description.key == "next_enabled_timer":
            _, attributes = _next_timer_sensor_payload(
                self._cloud.devices[self._device_id]
            )
            self._attr_extra_state_attributes = attributes
        elif description.attributes_fn is not None:
            self._attr_extra_state_attributes = description.attributes_fn(
                self._cloud.devices[self._device_id]
            )

        if not isinstance(description.unit_fn, type(None)):
            self._attr_native_unit_of_measurement = description.unit_fn(
                self._cloud.devices[self._device_id]
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        name_changed = self._update_placeholders()
        new_value = self.entity_description.value_fn(  # type: ignore
            self._cloud.devices[self._device_id]
        )
        current_attributes = getattr(self, "_attr_extra_state_attributes", None)
        new_attributes = current_attributes
        if self.entity_description.key == "next_enabled_timer":
            _, new_attributes = _next_timer_sensor_payload(
                self._cloud.devices[self._device_id]
            )
        elif self.entity_description.attributes_fn is not None:  # type: ignore
            new_attributes = self.entity_description.attributes_fn(  # type: ignore
                self._cloud.devices[self._device_id]
            )

        changed = (
            name_changed
            or new_value != self._attr_native_value
            or new_attributes != current_attributes
        )
        self._attr_native_value = new_value
        self._attr_extra_state_attributes = new_attributes
        self._async_write_if_changed(changed)

    def _update_placeholders(self) -> bool:
        """Update the name placeholders; return True when they changed."""
        placeholders_fn = self.entity_description.placeholders_fn  # type: ignore
        if placeholders_fn is None:
            return False
        placeholders = placeholders_fn(self._cloud.devices[self._device_id])
        if placeholders == getattr(self, "_attr_translation_placeholders", None):
            return False
        self._attr_translation_placeholders = placeholders
        # The name is a cached property built from the placeholders, and
        # setting the placeholders only clears their own cache.
        self.__dict__.pop("name", None)
        return True
