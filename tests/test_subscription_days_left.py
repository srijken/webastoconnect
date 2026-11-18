"""Tests for the days left until the subscription expires."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.util import dt as dt_util
import pytest

from custom_components.webastoconnect.sensor import SENSORS
from custom_components.webastoconnect.subscription import (
    subscription_days_left,
    subscription_issue,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def amsterdam_time_zone():
    """Count days in a time zone other than UTC."""
    previous = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Amsterdam"))
    yield
    dt_util.set_default_time_zone(previous)


@pytest.mark.parametrize(
    ("expiration", "expected"),
    [
        (NOW + timedelta(days=27), 27),
        (NOW, 0),
        # Later the same local day is still the last day.
        (datetime(2026, 11, 18, 22, 30, tzinfo=UTC), 0),
        # 23:30 UTC is already the next day in Amsterdam.
        (datetime(2026, 11, 18, 23, 30, tzinfo=UTC), 1),
        (NOW - timedelta(days=1), -1),
        (NOW - timedelta(days=400), -400),
    ],
    ids=["future", "today", "today_late", "tomorrow_local", "yesterday", "long_ago"],
)
def test_days_left(expiration: datetime, expected: int) -> None:
    """Days are counted from today to the local expiration date."""
    assert subscription_days_left(expiration, NOW) == expected


@pytest.mark.parametrize("expiration", [None, "2026-12-15", 1765800000])
def test_days_left_unknown(expiration) -> None:
    """No expiration datetime: unknown."""
    assert subscription_days_left(expiration, NOW) is None


@pytest.mark.parametrize("days", [30, 5, 0])
def test_days_left_matches_repair_issue(days: int) -> None:
    """The repair issue reports the same days left."""
    expiration = NOW + timedelta(days=days)
    issue = subscription_issue(expiration, "Camper", NOW)
    assert dict(issue.placeholders)["days_left"] == str(
        subscription_days_left(expiration, NOW)
    )


def test_sensor_description() -> None:
    """The sensor is an enabled diagnostic duration in days."""
    description = next(d for d in SENSORS if d.key == "subscription_days_left")
    assert description.translation_key == "subscription_days_left"
    assert description.native_unit_of_measurement == UnitOfTime.DAYS
    assert description.device_class is SensorDeviceClass.DURATION
    assert description.state_class is SensorStateClass.MEASUREMENT
    assert description.entity_category is EntityCategory.DIAGNOSTIC
    assert description.entity_registry_enabled_default is True
    assert description.value_fn(SimpleNamespace(subscription_expiration=None)) is None


def test_sensor_shows_whole_days() -> None:
    """Whole days are shown without the default duration decimals."""
    description = next(d for d in SENSORS if d.key == "subscription_days_left")
    assert description.suggested_display_precision == 0
