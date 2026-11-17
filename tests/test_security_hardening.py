"""Tests for the security hardening of credentials, attributes and services."""

from datetime import UTC, datetime
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

import custom_components.webastoconnect as integration
from custom_components.webastoconnect.api import (
    _credential_store_path,
    _load_credentials,
    _save_credentials,
)
from custom_components.webastoconnect.sensor import _next_timer_sensor_payload
from custom_components.webastoconnect.services import (
    _CREATE_TIMER_SCHEMA,
    _UPDATE_TIMER_SCHEMA,
)


def test_credentials_file_is_private_and_round_trips(tmp_path: Path) -> None:
    """The app credential file must only be readable by its owner."""
    path = tmp_path / ".storage" / "webasto_entry.json"

    _save_credentials(path, SimpleNamespace(client_id="id", client_secret="secret"))

    assert os.stat(path).st_mode & 0o777 == 0o600
    assert _load_credentials(path) == {"client_id": "id", "client_secret": "secret"}


@pytest.mark.asyncio
async def test_remove_entry_deletes_credentials(tmp_path: Path) -> None:
    """Removing the entry deletes the stored app credentials."""
    hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts))),
        async_add_executor_job=AsyncMock(side_effect=lambda func, *args: func(*args)),
    )
    entry = SimpleNamespace(entry_id="entry-1")
    path = Path(_credential_store_path(hass, entry))
    _save_credentials(path, SimpleNamespace(client_id="id", client_secret="secret"))

    await integration.async_remove_entry(hass, entry)
    assert not path.exists()

    # Removing again (file already gone) does not fail.
    await integration.async_remove_entry(hass, entry)


def test_timer_attributes_do_not_expose_coordinates() -> None:
    """Timer attributes say whether a location is set, not where it is."""
    webasto = SimpleNamespace(
        last_data={
            "outputs": [
                {
                    "line": "OUTH",
                    "timers": [
                        {
                            "type": "simple",
                            "start": 390,
                            "duration": 5400,
                            "repeat": 1,
                            "enabled": True,
                            "location": {"lat": "52.0", "lon": "5.0"},
                        }
                    ],
                }
            ]
        }
    )

    _, attributes = _next_timer_sensor_payload(
        webasto, now_utc=datetime(2026, 11, 17, tzinfo=UTC)
    )

    timer = attributes["timers"][0]
    assert timer["has_location"] is True
    assert "latitude" not in timer
    assert "longitude" not in timer
    assert "52.0" not in str(attributes)


@pytest.mark.parametrize("schema", [_CREATE_TIMER_SCHEMA, _UPDATE_TIMER_SCHEMA])
@pytest.mark.parametrize(
    "bad",
    [
        {"duration_minutes": 1441},
        {"repeat": 128},
        {"latitude": "not-a-number"},
        {"latitude": "91"},
        {"location": {"latitude": 0, "longitude": 181}},
    ],
)
def test_timer_schemas_reject_out_of_range_values(schema, bad) -> None:
    """Service schemas bound durations, weekday masks and coordinates."""
    base = {"device_id": "abc", "start_time": "07:30", "duration_minutes": 30}
    if schema is _UPDATE_TIMER_SCHEMA:
        base = {"device_id": "abc", "timer_index": 0}

    with pytest.raises(vol.Invalid):
        schema({**base, **bad})


def test_timer_schemas_accept_valid_and_clearing_values() -> None:
    """Valid values pass, and empty legacy coordinates can still clear."""
    created = _CREATE_TIMER_SCHEMA(
        {
            "device_id": "abc",
            "line": "ventilation",
            "start_time": "07:30",
            "duration_minutes": 1440,
            "repeat": 127,
            "latitude": "52.1",
            "longitude": 5.2,
        }
    )
    assert created["latitude"] == "52.1"
    assert created["longitude"] == "5.2"
    assert created["line"] == "ventilation"

    updated = _UPDATE_TIMER_SCHEMA(
        {"device_id": "abc", "timer_index": 0, "latitude": "", "longitude": None}
    )
    assert updated["latitude"] == ""
    assert updated["longitude"] is None


def test_malformed_timer_is_skipped() -> None:
    """A timer with null or non-numeric fields does not break the sensor."""
    webasto = SimpleNamespace(
        last_data={
            "outputs": [
                {
                    "line": "OUTH",
                    "timers": [
                        {"type": "simple", "start": "x", "duration": 60, "repeat": 0},
                        {
                            "type": "simple",
                            "start": None,
                            "duration": 60,
                            "repeat": 0,
                            "enabled": True,
                        },
                        {
                            "type": "simple",
                            "start": 390,
                            "duration": 60,
                            "repeat": 1,
                            "enabled": True,
                        },
                    ],
                }
            ]
        }
    )

    _, attributes = _next_timer_sensor_payload(
        webasto, now_utc=datetime(2026, 11, 17, tzinfo=UTC)
    )

    assert [timer["index"] for timer in attributes["timers"]] == [1, 2]
