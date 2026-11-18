"""Tests for smart timers in the timer sensor and the timer actions."""

from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.exceptions import ServiceValidationError
from pywebasto import SimpleTimer, SmartTimer, UnknownTimer
import pytest
import voluptuous as vol
import yaml

from custom_components.webastoconnect import services
from custom_components.webastoconnect.sensor import _next_timer_sensor_payload

SMART = {
    "type": "smart",
    "repeat": 31,
    "start": 1360,  # departure 22:40 UTC
    "maxDuration": 1800,
    "comfortLevel": 5,
    "location": {"lat": "1.5", "lon": "2.5"},
    "enabled": True,
}
SIMPLE = {
    "type": "simple",
    "repeat": 0,
    "start": 600,
    "duration": 1800,
    "enabled": True,
}


def _webasto(outputs: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(last_data={"outputs": outputs, "disabled_outputs": []})


def test_sensor_lists_smart_timers_with_their_own_index() -> None:
    """Smart and simple timers are indexed per type and line, as the actions."""
    webasto = _webasto(
        [
            {
                "line": "OUTH",
                "timers": [
                    SMART,
                    SIMPLE,
                    {"type": "future", "start": 1},
                    {**SMART, "start": 420, "enabled": False},
                ],
            },
            {"line": "OUTV", "timers": [{**SIMPLE, "start": 700}]},
        ]
    )

    _, attributes = _next_timer_sensor_payload(
        webasto, now_utc=datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    )

    timers = attributes["timers"]
    assert [(t["type"], t["line_code"], t["index"]) for t in timers] == [
        ("smart", "OUTH", 0),
        ("simple", "OUTH", 0),
        ("smart", "OUTH", 1),
        ("simple", "OUTV", 0),
    ]
    smart = timers[0]
    assert smart == {
        "index": 0,
        "type": "smart",
        "line": "Heater",
        "line_code": "OUTH",
        "departure": 1360,
        "departure_hhmm_utc": "22:40",
        "max_duration": 1800,
        "comfort_level": 5,
        "repeat": 31,
        "enabled": True,
        "has_location": True,
        # 2026-11-18 is a Wednesday, included in Monday..Friday.
        "next_run_utc": "2026-11-18T22:40:00+00:00",
    }
    assert "latitude" not in str(attributes)
    assert "1.5" not in str(attributes)
    simple = timers[1]
    assert simple["start_hhmm_utc"] == "10:00"
    assert simple["duration"] == 1800
    assert "departure" not in simple


def test_sensor_next_timer_can_be_a_smart_timer() -> None:
    """The next run of a smart timer is its departure time."""
    webasto = _webasto([{"line": "OUTH", "timers": [SIMPLE, SMART]}])

    state, attributes = _next_timer_sensor_payload(
        webasto, now_utc=datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    )

    assert state == datetime(2026, 11, 18, 22, 40, tzinfo=UTC)
    assert attributes["next_timer_index"] == 0
    assert attributes["next_timer_type"] == "smart"
    assert attributes["next_timer"]["departure_hhmm_utc"] == "22:40"


def test_sensor_without_smart_timers_keeps_simple_attributes() -> None:
    """Users without smart timers keep the same indexes and values."""
    webasto = _webasto([{"line": "OUTH", "timers": [SIMPLE, {**SIMPLE, "start": 9}]}])

    state, attributes = _next_timer_sensor_payload(
        webasto, now_utc=datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    )

    assert [t["index"] for t in attributes["timers"]] == [0, 1]
    assert attributes["next_timer_type"] == "simple"
    assert state == datetime(2026, 11, 19, 0, 9, tzinfo=UTC)


def test_sensor_skips_malformed_smart_timer_but_keeps_indexes() -> None:
    """A malformed smart timer is skipped without shifting later indexes."""
    webasto = _webasto(
        [{"line": "OUTH", "timers": [{**SMART, "maxDuration": "x"}, SMART]}]
    )

    _, attributes = _next_timer_sensor_payload(
        webasto, now_utc=datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    )

    assert [t["index"] for t in attributes["timers"]] == [1]


class _Coordinator:
    """Coordinator stub around a full timer list."""

    def __init__(self, timers: list) -> None:
        self.cloud = SimpleNamespace(
            get_timers=AsyncMock(
                return_value=[t for t in timers if isinstance(t, SimpleTimer)]
            ),
            save_timers=AsyncMock(),
            get_all_timers=AsyncMock(return_value=list(timers)),
            save_all_timers=AsyncMock(),
        )
        self.async_update_listeners = Mock()

    async def async_execute_cloud_call(self, cloud_call, *args, **kwargs):
        return await cloud_call(*args, **kwargs)


def _timers() -> list:
    return [
        SimpleTimer(start=600, duration=1800, repeat=0),
        SmartTimer(start=1360, max_duration=1800, comfort_level=5, repeat=0),
        UnknownTimer(data={"type": "future"}),
        SmartTimer(start=420, max_duration=600, comfort_level=5, repeat=1),
    ]


def _utc_hass() -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))


async def test_create_smart_timer_appends_to_full_list() -> None:
    """A new smart timer is appended; every other timer is kept."""
    coordinator = _Coordinator(_timers())
    device = SimpleNamespace(device_id="dev1", is_ventilation=False)
    timer = SmartTimer(start=500, max_duration=1200, comfort_level=5, repeat=0)

    await services.async_create_smart_timer(coordinator, device, timer)

    kwargs = coordinator.cloud.save_all_timers.await_args.kwargs
    assert kwargs["line"].value == "OUTH"
    assert kwargs["timers"] == [*_timers(), timer]
    coordinator.cloud.save_timers.assert_not_awaited()
    coordinator.async_update_listeners.assert_called_once()


async def test_update_smart_timer_counts_smart_timers_only() -> None:
    """Index 1 is the second smart timer; simple and unknown timers stay."""
    coordinator = _Coordinator(_timers())
    device = SimpleNamespace(device_id="dev1")

    await services.async_update_smart_timer(
        coordinator,
        device,
        1,
        {"departure_time": "08:15", "max_duration_minutes": 45, "comfort_level": 6},
        "ventilation",
        hass=_utc_hass(),
    )

    kwargs = coordinator.cloud.save_all_timers.await_args.kwargs
    assert kwargs["line"].value == "OUTV"
    expected = _timers()
    expected[3] = SmartTimer(start=495, max_duration=2700, comfort_level=6, repeat=1)
    assert kwargs["timers"] == expected


async def test_update_smart_timer_invalid_index() -> None:
    """An index beyond the smart timers raises a translated error."""
    coordinator = _Coordinator(_timers())

    with pytest.raises(ServiceValidationError) as err:
        await services.async_update_smart_timer(
            coordinator, SimpleNamespace(device_id="dev1"), 2, {"enabled": False}
        )

    assert err.value.translation_key == "invalid_timer_index"
    assert err.value.translation_placeholders["max_index"] == "1"
    coordinator.cloud.save_all_timers.assert_not_awaited()


async def test_update_smart_timer_without_smart_timers() -> None:
    """A line without smart timers reports that there is nothing to change."""
    coordinator = _Coordinator([SimpleTimer(start=600, duration=1800, repeat=0)])

    with pytest.raises(ServiceValidationError) as err:
        await services.async_update_smart_timer(
            coordinator, SimpleNamespace(device_id="dev1"), 0, {"enabled": False}
        )

    assert err.value.translation_key == "no_timers"


async def test_delete_smart_timer_by_smart_index() -> None:
    """delete_timer with type smart removes the n-th smart timer only."""
    coordinator = _Coordinator(_timers())
    device = SimpleNamespace(device_id="dev1", is_ventilation=False)

    await services.async_delete_timer(
        coordinator, device, 0, line="heater", timer_type="smart"
    )

    timers = coordinator.cloud.save_all_timers.await_args.kwargs["timers"]
    assert timers == [_timers()[0], _timers()[2], _timers()[3]]
    coordinator.cloud.save_timers.assert_not_awaited()


async def test_delete_simple_timer_ignores_smart_timers() -> None:
    """The default type keeps the simple-only index and simple save path."""
    coordinator = _Coordinator(_timers())
    device = SimpleNamespace(device_id="dev1", is_ventilation=False)

    await services.async_delete_timer(coordinator, device, 0)

    assert coordinator.cloud.save_timers.await_args.kwargs["timers"] == []
    coordinator.cloud.save_all_timers.assert_not_awaited()


async def test_delete_with_line_uses_that_line() -> None:
    """A line given to delete_timer overrides the active output."""
    coordinator = _Coordinator(_timers())
    device = SimpleNamespace(device_id="dev1", is_ventilation=False)

    await services.async_delete_timer(coordinator, device, 0, line="ventilation")

    assert coordinator.cloud.save_timers.await_args.kwargs["line"].value == "OUTV"


def test_coerce_smart_timer_converts_local_departure_time() -> None:
    """The local departure time is stored in minutes after midnight UTC."""
    hass = SimpleNamespace(config=SimpleNamespace(time_zone="Asia/Kolkata"))

    timer = services._coerce_smart_timer(
        {
            "departure_time": "07:30",
            "max_duration_minutes": 30,
            "comfort_level": 5,
            "repeat_days": ["monday", "sunday"],
            "location": {"latitude": 1.5, "longitude": 2.5},
        },
        hass=hass,
    )

    # 07:30 IST (UTC+5:30) is 02:00 UTC.
    assert timer == SmartTimer(
        start=120,
        max_duration=1800,
        comfort_level=5,
        repeat=65,
        latitude="1.5",
        longitude="2.5",
    )


def test_coerce_smart_timer_keeps_existing_values() -> None:
    """Only the given fields change; clear_location removes the location."""
    existing = SmartTimer(
        start=1360,
        max_duration=1800,
        comfort_level=5,
        repeat=31,
        latitude="1.5",
        longitude="2.5",
    )

    timer = services._coerce_smart_timer(
        {"enabled": False, "clear_location": True}, existing=existing
    )

    assert timer == SmartTimer(
        start=1360, max_duration=1800, comfort_level=5, repeat=31, enabled=False
    )


def test_coerce_smart_timer_invalid_departure_time() -> None:
    """A departure time that is not HH:MM raises its own translated error."""
    with pytest.raises(ServiceValidationError) as err:
        services._coerce_smart_timer(
            {"departure_time": "late", "max_duration_minutes": 30},
            hass=_utc_hass(),
        )

    assert err.value.translation_key == "invalid_departure_time"
    assert err.value.translation_placeholders == {"departure_time": "late"}


def test_coerce_smart_timer_missing_values() -> None:
    """Without an existing timer every smart timer value is needed."""
    with pytest.raises(ServiceValidationError) as err:
        services._coerce_smart_timer({"max_duration_minutes": 30})

    assert err.value.translation_key == "missing_smart_timer_values"


BASE = {"device_id": "dev", "departure_time": "07:30"}


@pytest.mark.parametrize("minutes", [1, 120])
def test_create_smart_schema_accepts_duration_bounds(minutes: int) -> None:
    """Maximum duration accepts 1 to 120 minutes; comfort level defaults to 5."""
    data = services._CREATE_SMART_TIMER_SCHEMA(
        {**BASE, "max_duration_minutes": minutes}
    )

    assert data["max_duration_minutes"] == minutes
    assert data["comfort_level"] == 5
    assert data["repeat_days"] == []
    assert data["enabled"] is True
    assert "line" not in data


@pytest.mark.parametrize(
    "extra",
    [
        {"max_duration_minutes": 0},
        {"max_duration_minutes": 121},
        {"max_duration_minutes": 30, "comfort_level": 0},
        {"max_duration_minutes": 30, "comfort_level": 11},
        {"max_duration_minutes": 30, "comfort_level": "high"},
        {"max_duration_minutes": 30, "repeat_days": ["someday"]},
        {"max_duration_minutes": 30, "line": "OUT1"},
    ],
)
def test_create_smart_schema_rejects_out_of_range(extra: dict) -> None:
    """Values outside the accepted ranges are rejected by the schema."""
    with pytest.raises(vol.Invalid):
        services._CREATE_SMART_TIMER_SCHEMA({**BASE, **extra})


def test_create_smart_schema_requires_departure_and_duration() -> None:
    """Departure time and maximum duration are required."""
    with pytest.raises(vol.Invalid):
        services._CREATE_SMART_TIMER_SCHEMA({"device_id": "dev"})
    with pytest.raises(vol.Invalid):
        services._CREATE_SMART_TIMER_SCHEMA(BASE)


def test_update_smart_schema_bounds() -> None:
    """update_smart_timer defaults to the heater line and checks the ranges."""
    data = services._UPDATE_SMART_TIMER_SCHEMA({"device_id": "dev", "timer_index": 0})
    assert data["line"] == "heater"
    for extra in ({"max_duration_minutes": 121}, {"comfort_level": 11}):
        with pytest.raises(vol.Invalid):
            services._UPDATE_SMART_TIMER_SCHEMA(
                {"device_id": "dev", "timer_index": 0, **extra}
            )


def test_delete_schema_defaults_to_simple_without_line() -> None:
    """delete_timer keeps its old meaning when type and line are omitted."""
    data = services._DELETE_TIMER_SCHEMA({"device_id": "dev", "timer_index": 0})

    assert data["type"] == "simple"
    assert "line" not in data
    with pytest.raises(vol.Invalid):
        services._DELETE_TIMER_SCHEMA(
            {"device_id": "dev", "timer_index": 0, "type": "eco"}
        )


LANGUAGES = ("cs", "da", "de", "en", "es", "fi", "fr", "nb", "nl")
COMPONENT_DIR = Path(services.__file__).parent
CARD_KEYS = {
    "timer_type",
    "timer_type_simple",
    "timer_type_smart",
    "departure_time",
    "max_duration_minutes",
    "comfort_level",
    "smart_timer_help",
    "timer_departure",
    "timer_max_duration",
    "timer_comfort",
}


@pytest.mark.parametrize("language", LANGUAGES)
def test_smart_timer_translations_exist(language: str) -> None:
    """Every language translates the smart timer actions, options and errors."""
    data = json.loads(
        (COMPONENT_DIR / "translations" / f"{language}.json").read_text("utf-8")
    )
    for action in ("create_smart_timer", "update_smart_timer"):
        assert data["services"][action]["name"]
        assert data["services"][action]["description"]
        for field in ("departure_time", "max_duration_minutes", "comfort_level"):
            assert data["services"][action]["fields"][field]["name"]
    for field in ("line", "type"):
        assert data["services"]["delete_timer"]["fields"][field]["description"]
    assert set(data["selector"]["timer_type"]["options"]) == {"simple", "smart"}
    assert all(data["selector"]["timer_type"]["options"].values())
    assert (
        "{departure_time}" in (data["exceptions"]["invalid_departure_time"]["message"])
    )
    assert data["exceptions"]["missing_smart_timer_values"]["message"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_smart_timer_card_translations_exist(language: str) -> None:
    """Every card language has the smart timer strings and placeholders."""
    path = COMPONENT_DIR.parents[1] / "card-src" / "translations" / f"{language}.json"
    ui = json.loads(path.read_text("utf-8"))["card"]["ui"]

    assert set(ui) >= CARD_KEYS
    assert all(ui[key] for key in CARD_KEYS)
    assert "{time}" in ui["timer_departure"]
    assert "{minutes}" in ui["timer_max_duration"]
    assert "{level}" in ui["timer_comfort"]


def test_services_yaml_smart_timer_bounds_match_schema() -> None:
    """The UI selectors use the same bounds as the action schemas."""
    definitions = yaml.safe_load((COMPONENT_DIR / "services.yaml").read_text("utf-8"))

    for action in ("create_smart_timer", "update_smart_timer"):
        fields = definitions[action]["fields"]
        duration = fields["max_duration_minutes"]["selector"]["number"]
        comfort = fields["comfort_level"]["selector"]["number"]
        assert (duration["min"], duration["max"]) == (
            1,
            services.MAX_SMART_DURATION_MINUTES,
        )
        assert (comfort["min"], comfort["max"]) == (
            services.MIN_COMFORT_LEVEL,
            services.MAX_COMFORT_LEVEL,
        )
    delete_type = definitions["delete_timer"]["fields"]["type"]["selector"]["select"]
    assert delete_type["options"] == list(services.TIMER_TYPES)
    assert delete_type["translation_key"] == "timer_type"
