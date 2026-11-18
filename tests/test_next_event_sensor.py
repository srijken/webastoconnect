"""Tests for the next event sensor helpers (backend `nextEvent`)."""

from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util
import pytest

from custom_components.webastoconnect.sensor import (
    _next_event,
    _next_event_attributes,
    _next_event_local_time,
)

# Sanitized `nextEvent` values observed on Tuesday 2026-11-17 (pywebasto
# docs/protocol.md). `time` is the timer start in minutes after midnight UTC;
# the meaning of `day` is unknown.
OBS_EVERY_DAY = {"line": "OUTH", "type": "normal", "day": 4, "time": 1349}
OBS_MONDAY = {"line": "OUTH", "type": "normal", "day": 1, "time": 390}
OBS_SMART_ONE_OFF = {"line": "OUTH", "type": "smart", "day": 4, "time": 1360}

WINTER = datetime(2026, 11, 17, 22, 37, tzinfo=UTC)
SUMMER = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def amsterdam_time_zone() -> Generator[None]:
    """Use the timezone of the observations as Home Assistant's timezone."""
    previous = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(ZoneInfo("Europe/Amsterdam"))
    yield
    dt_util.set_default_time_zone(previous)


def _device(next_event: object = None, *, include: bool = True) -> SimpleNamespace:
    """Return a device like pywebasto-ng 3.1.1: no `next_event` property."""
    last_data: dict = {"outputs": []}
    if include:
        last_data["nextEvent"] = next_event
    return SimpleNamespace(last_data=last_data)


@pytest.mark.parametrize(
    ("raw", "local_time", "attributes"),
    [
        (
            OBS_EVERY_DAY,
            "23:29",
            {"line": "Heater", "type": "simple", "time_utc": "22:29", "day_raw": 4},
        ),
        (
            OBS_MONDAY,
            "07:30",
            {"line": "Heater", "type": "simple", "time_utc": "06:30", "day_raw": 1},
        ),
        (
            OBS_SMART_ONE_OFF,
            "23:40",
            {"line": "Heater", "type": "smart", "time_utc": "22:40", "day_raw": 4},
        ),
    ],
)
def test_observations(raw: dict, local_time: str, attributes: dict) -> None:
    """The observed events give the local time of day and raw attributes."""
    device = _device(raw)

    assert _next_event_local_time(device, now_utc=WINTER) == local_time
    assert _next_event_attributes(device) == attributes


def test_conversion_uses_todays_offset() -> None:
    """In summer the same UTC time is shown with the summer offset (UTC+2)."""
    device = _device(OBS_EVERY_DAY)

    assert _next_event_local_time(device, now_utc=SUMMER) == "00:29"
    assert _next_event_local_time(_device(OBS_MONDAY), now_utc=SUMMER) == "08:30"


@pytest.mark.parametrize("raw", [{}, None, [], "OUTH", 5])
def test_no_next_event(raw: object) -> None:
    """An empty or malformed `nextEvent` means there is no next event."""
    device = _device(raw)

    assert _next_event(device) is None
    assert _next_event_local_time(device, now_utc=WINTER) is None
    assert _next_event_attributes(device) is None


def test_missing_field_and_data() -> None:
    """No `nextEvent` field or no data at all means no next event."""
    assert _next_event(_device(include=False)) is None
    assert _next_event(SimpleNamespace(last_data=None)) is None
    assert _next_event(SimpleNamespace()) is None


def test_wrong_field_types_are_ignored() -> None:
    """Values of the wrong type or out of range become None."""
    device = _device({"line": 1, "type": None, "day": "4", "time": True})

    assert _next_event_local_time(device, now_utc=WINTER) is None
    assert _next_event_attributes(device) == {
        "line": None,
        "type": None,
        "time_utc": None,
        "day_raw": None,
    }
    assert _next_event(_device({"time": 1440}))["time_utc_minutes"] is None
    assert _next_event(_device({"time": -1}))["time_utc_minutes"] is None


def test_unknown_type_and_line_are_passed_through() -> None:
    """Values that were not observed are shown as-is, not guessed."""
    device = _device({"line": "OUTV", "type": "other", "day": 2, "time": 0})

    assert _next_event_local_time(device, now_utc=WINTER) == "01:00"
    assert _next_event_attributes(device) == {
        "line": "Ventilation",
        "type": "other",
        "time_utc": "00:00",
        "day_raw": 2,
    }


def test_uses_library_property_when_available() -> None:
    """A library with `next_event` is used instead of parsing the raw data."""
    library_event = SimpleNamespace(
        line="OUTH",
        type="smart",
        time_utc_minutes=1360,
        day=4,
        raw=OBS_SMART_ONE_OFF,
    )
    device = SimpleNamespace(
        next_event=library_event, last_data={"nextEvent": OBS_MONDAY}
    )

    assert _next_event(device) == {
        "line": "OUTH",
        "type": "smart",
        "time_utc_minutes": 1360,
        "day": 4,
    }
    # A library returning None (empty `nextEvent`) falls back to the data,
    # which is then empty as well.
    assert _next_event(SimpleNamespace(next_event=None, last_data={})) is None
