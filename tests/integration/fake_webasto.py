"""A pywebasto client that answers from in-memory account data.

`FakeWebastoConnect` subclasses the real `WebastoConnect`, so device parsing,
timer handling and output commands run the library code. Only the network is
replaced: the login in `connect()` and the app backend transport
(`_app_call`). The payload shapes are sanitized from pywebasto's own tests
(`tests/test_device_status_fields.py`, `tests/test_smart_timers.py`); ids,
check ids and locations are made up.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any

from pywebasto import WebastoConnect
from pywebasto.exceptions import UnauthorizedException

DEVICE_ID = "1234567890"
DEVICE_NAME = "Defender"
EMAIL = "Driver@Example.com"
PASSWORD = "correct-horse"

SIMPLE_TIMER = {
    "type": "simple",
    "repeat": 127,
    "start": 1349,
    "duration": 5400,
    "enabled": False,
}
SMART_TIMER = {
    "type": "smart",
    "repeat": 0,
    "start": 1360,
    "maxDuration": 1800,
    "comfortLevel": 5,
    "enabled": True,
}

# One device of the app backend `/all?api_v=8` response.
DEVICE_APP_DATA: dict[str, Any] = {
    "id": DEVICE_ID,
    "name": DEVICE_NAME,
    "assocStatus": "ok",
    "temperature": "9C",
    "voltage": "10.1V",
    "outputs": [
        {
            "line": "OUTH",
            "name": "Heater",
            "icon": "car_heat",
            "state": "OFF",
            "ontime": 0,
            "parking_heating_mode_available": True,
            "ventilation_mode_available": True,
            "eco_mode_available": False,
            "boost_mode_available": False,
            "water_heater_continuous_heating": False,
            "timers": [SIMPLE_TIMER, SMART_TIMER],
        }
    ],
    "seen": 97,
    "location": {"state": "ON", "lat": 1.5, "lon": 2.5},
    "subscription": {"status": "ok", "expiration": 1796831722, "message": ""},
    "alarms": "ON",
    "connection_lost": False,
    "vwarn": True,
    "location_services": "ON",
    "geofence_enabled": True,
}

# Web API settings of the device.
DEVICE_SETTINGS: dict[str, Any] = {
    "hw_version": "35.0",
    "sw_version": 152,
    "sw_variant": 3,
    "sw_external": "141",
    "settings_tab": [
        {
            "group": "general",
            "options": [
                {"key": "allow_GPS", "type": "bool", "value": True},
                {
                    "key": "geofence",
                    "type": "geofence",
                    "enabled": True,
                    "value": 1.0,
                    "unit": "km",
                },
                {"key": "low_voltage_cutoff", "type": "range", "value": 10.3},
                {"key": "def_output", "type": "select", "value": "automatic"},
                {"key": "ext_temp_comp", "type": "range", "value": 0.0},
                {
                    "key": "sig_lost_off",
                    "type": "range",
                    "enabled": False,
                    "value": 10,
                    "unit": "mins",
                },
            ],
        },
        {
            "group": "webasto",
            "options": [
                {
                    "key": "webasto_emul_mode",
                    "type": "select",
                    "value": "thermoconnect",
                },
                {"key": "OUTH", "type": "output", "timeout": 7200},
                {"key": "OUTV", "type": "output", "timeout": 3600},
            ],
        },
    ],
}

_COMMAND_STATES = {
    "OUT H ON": ("OUTH", "ON"),
    "OUT H OFF": ("OUTH", "OFF"),
    "OUT V ON": ("OUTV", "ON"),
    "OUT V OFF": ("OUTV", "OFF"),
    "OUT 1 ON": ("OUT1", "ON"),
    "OUT 1 OFF": ("OUT1", "OFF"),
    "OUT 2 ON": ("OUT2", "ON"),
    "OUT 2 OFF": ("OUT2", "OFF"),
}


@dataclass
class FakeWebastoCloud:
    """State of the fake Webasto cloud, shared by every client instance."""

    password: str = PASSWORD
    devices: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {DEVICE_ID: deepcopy(DEVICE_APP_DATA)}
    )
    settings: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {DEVICE_ID: deepcopy(DEVICE_SETTINGS)}
    )
    # Raised by connect() before checking the password.
    connect_error: Exception | None = None
    # Raised by every app backend request (used by coordinator updates).
    request_error: Exception | None = None
    clients: list[FakeWebastoConnect] = field(default_factory=list)
    requests: list[tuple[str, str, Any]] = field(default_factory=list)

    def client(self, **kwargs: Any) -> FakeWebastoConnect:
        """Return a new client; used in place of the WebastoConnect class."""
        client = FakeWebastoConnect(self, **kwargs)
        self.clients.append(client)
        return client

    def timers(self, line: str = "OUTH") -> list[dict[str, Any]]:
        """Return the stored timers of an output line of the device."""
        for output in self.devices[DEVICE_ID]["outputs"]:
            if output["line"] == line:
                return output.get("timers", [])
        return []


class FakeWebastoConnect(WebastoConnect):
    """WebastoConnect that talks to a `FakeWebastoCloud` instead of the network."""

    def __init__(self, cloud: FakeWebastoCloud, **kwargs: Any) -> None:
        """Initialize; app credentials are known and every update is fresh."""
        super().__init__(
            client_id="fake-client-id",
            client_secret="fake-client-secret",
            refresh_interval=0,
            **kwargs,
        )
        self.fake_cloud = cloud
        self.password = kwargs.get("password")
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        """Log in, then load the account data and the device settings."""
        if self.fake_cloud.connect_error is not None:
            raise self.fake_cloud.connect_error
        if self.password != self.fake_cloud.password:
            raise UnauthorizedException("Invalid username or password")
        await self.update(force=True)
        for device_id, device in self.devices.items():
            device.settings = deepcopy(self.fake_cloud.settings.get(device_id, {}))
        self.connected = True

    async def close(self) -> None:
        """Record that the client was closed."""
        self.closed = True
        await super().close()

    async def _app_call(
        self,
        method: str,
        path: str,
        payload: str | None = None,
        auth: bool = True,
        expect_json: bool = False,
        extra_headers: dict | None = None,
    ) -> dict | str | None:
        """Answer an app backend request from the fake cloud state."""
        cloud = self.fake_cloud
        cloud.requests.append((method, path, payload))
        if cloud.request_error is not None:
            raise cloud.request_error

        if method == "GET" and path.endswith("/all?api_v=8"):
            return {"devices": deepcopy(list(cloud.devices.values()))}

        device_id = path.split("/device/", 1)[-1].split("/", 1)[0]
        if method == "POST" and path.endswith("/timers2"):
            body = json.loads(payload or "{}")
            line = f"OUT{body['output']}"
            for output in cloud.devices[device_id]["outputs"]:
                if output["line"] == line:
                    output["timers"] = body["timers"]
            return None
        if method == "POST" and path.endswith("/cmd"):
            line, state = _COMMAND_STATES[payload or ""]
            for output in cloud.devices[device_id]["outputs"]:
                if output["line"] == line:
                    output["state"] = state
            return None

        raise AssertionError(f"Unexpected Webasto request: {method} {path}")

    async def _call(self, *args: Any, **kwargs: Any) -> Any:
        """Fail: the web API is not used, no web API session is ever set."""
        raise AssertionError(f"Unexpected Webasto web API request: {args}")
