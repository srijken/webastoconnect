"""Tests for credential storage: migration, reauth and reconfigure."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import AbortFlow
from pywebasto.exceptions import (
    InvalidRequestException,
    TooManyRequestsException,
    UnauthorizedException,
)

import custom_components.webastoconnect as integration
import custom_components.webastoconnect.config_flow as config_flow
from custom_components.webastoconnect.config_flow import WebastoConnectConfigFlow


@pytest.fixture(autouse=True)
def mock_integration_version(monkeypatch) -> None:
    """Provide integration metadata for the flows."""
    monkeypatch.setattr(
        config_flow,
        "async_get_integration",
        AsyncMock(return_value=SimpleNamespace(version="test")),
    )


@pytest.fixture(autouse=True)
def mock_credential_store(monkeypatch) -> None:
    """Keep the stored app credentials out of the flow tests."""
    monkeypatch.setattr("custom_components.webastoconnect.api.Store", Mock())


def _fake_webasto(monkeypatch, connect_error: Exception | None = None) -> Mock:
    """Patch WebastoConnect with a fake and return the constructor mock."""
    init_mock = Mock()

    class FakeWebasto:
        """Minimal fake Webasto client."""

        def __init__(self, *args, **kwargs) -> None:
            init_mock(*args, **kwargs)

        async def connect(self) -> None:
            if connect_error is not None:
                raise connect_error

        async def close(self) -> None:
            pass

    monkeypatch.setattr(config_flow, "WebastoConnect", FakeWebasto)
    return init_mock


def _flow(entry) -> WebastoConnectConfigFlow:
    """Build a config flow bound to an existing entry."""
    flow = WebastoConnectConfigFlow()
    flow.hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda *parts: "/".join(parts))
    )
    flow._get_reauth_entry = Mock(return_value=entry)
    flow._get_reconfigure_entry = Mock(return_value=entry)
    flow.async_update_reload_and_abort = Mock(return_value={"type": "abort"})
    flow.async_show_form = Mock(return_value={"type": "form"})
    return flow


def _entry() -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="entry-1",
        data={"email": "user@test", "password": "old"},
        options={},
    )


def test_config_flow_has_no_options_flow() -> None:
    """Credentials are no longer edited through an options flow."""
    assert "async_get_options_flow" not in WebastoConnectConfigFlow.__dict__
    assert not hasattr(config_flow, "WebastoConnectOptionsFlow")
    assert WebastoConnectConfigFlow.MINOR_VERSION == 2


@pytest.mark.parametrize("step", ["reauth_confirm", "reconfigure"])
@pytest.mark.asyncio
async def test_password_step_form_does_not_prefill_password(step: str) -> None:
    """The password form asks only for a password and does not pre-fill it."""
    flow = _flow(_entry())

    await getattr(flow, f"async_step_{step}")()

    kwargs = flow.async_show_form.call_args.kwargs
    assert kwargs["step_id"] == step
    assert list(kwargs["data_schema"].schema) == ["password"]
    assert next(iter(kwargs["data_schema"].schema)).default is vol.UNDEFINED
    assert kwargs["description_placeholders"] == {"email": "user@test"}


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        ("reauth_confirm", "reauth_successful"),
        ("reconfigure", "reconfigure_successful"),
    ],
)
@pytest.mark.asyncio
async def test_password_step_stores_password_in_entry_data(
    monkeypatch, step: str, reason: str
) -> None:
    """A valid password is stored in the entry data, never in the options."""
    init_mock = _fake_webasto(monkeypatch)
    entry = _entry()
    flow = _flow(entry)

    result = await getattr(flow, f"async_step_{step}")({"password": "new"})

    assert init_mock.call_args.kwargs["username"] == "user@test"
    assert init_mock.call_args.kwargs["password"] == "new"
    assert "credential_load" in init_mock.call_args.kwargs
    flow.async_update_reload_and_abort.assert_called_once_with(
        entry, data_updates={"password": "new"}, reason=reason
    )
    assert result == {"type": "abort"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (UnauthorizedException("bad"), "invalid_auth"),
        (InvalidRequestException("bad"), "cannot_connect"),
        (TooManyRequestsException("slow"), "ratelimit"),
    ],
)
@pytest.mark.asyncio
async def test_password_step_shows_errors(monkeypatch, error, expected) -> None:
    """Login failures keep the form open with an error."""
    _fake_webasto(monkeypatch, connect_error=error)
    flow = _flow(_entry())

    result = await flow.async_step_reconfigure({"password": "new"})

    flow.async_update_reload_and_abort.assert_not_called()
    assert flow.async_show_form.call_args.kwargs["errors"] == {"base": expected}
    assert result == {"type": "form"}


@pytest.mark.asyncio
async def test_migration_moves_credentials_from_options_to_data() -> None:
    """Entries written by the old options flow get their credentials moved."""
    entry = SimpleNamespace(
        entry_id="entry-1",
        version=1,
        minor_version=1,
        data={"email": "user@test", "password": "old"},
        options={"email": "user@test", "password": "changed", "other": 1},
    )
    update = Mock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))

    assert await integration.async_migrate_entry(hass, entry) is True

    update.assert_called_once_with(
        entry,
        data={"email": "user@test", "password": "changed"},
        options={"other": 1},
        minor_version=2,
    )


@pytest.mark.asyncio
async def test_migration_skips_current_entries() -> None:
    """Entries already on 1.2 are left alone."""
    entry = SimpleNamespace(entry_id="entry-1", version=1, minor_version=2)
    update = Mock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))

    assert await integration.async_migrate_entry(hass, entry) is True
    update.assert_not_called()


def _user_flow(
    existing_unique_ids: set[str] | None = None,
    existing_emails: list[str] | None = None,
) -> WebastoConnectConfigFlow:
    """Build a user-step config flow with the given existing entries."""
    existing_unique_ids = existing_unique_ids or set()
    flow = WebastoConnectConfigFlow()
    flow.handler = "webastoconnect"
    flow.context = {"source": "user"}
    flow.hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda *parts: "/".join(parts)),
        config_entries=SimpleNamespace(
            async_entry_for_domain_unique_id=lambda domain, unique_id: (
                SimpleNamespace(source="user", data={})
                if unique_id in existing_unique_ids
                else None
            )
        ),
    )
    flow._async_in_progress = Mock(return_value=[])
    flow._async_current_entries = Mock(
        return_value=[
            SimpleNamespace(data={"email": email}) for email in existing_emails or []
        ]
    )
    flow.async_create_entry = Mock(return_value={"type": "create_entry"})
    flow.async_abort = Mock(
        side_effect=lambda reason: {"type": "abort", "reason": reason}
    )
    flow.async_show_form = Mock(return_value={"type": "form"})
    return flow


@pytest.mark.asyncio
async def test_user_step_uses_normalized_email_as_unique_id(monkeypatch) -> None:
    """A new entry gets the lowercase email as unique ID."""
    init_mock = _fake_webasto(monkeypatch)
    flow = _user_flow()

    result = await flow.async_step_user({"email": "  User@Test.COM ", "password": "pw"})

    assert result == {"type": "create_entry"}
    assert flow.unique_id == "user@test.com"
    # Surrounding spaces are removed; the login uses the address as typed.
    assert init_mock.call_args.kwargs["username"] == "User@Test.COM"
    flow.async_create_entry.assert_called_once_with(
        title="User@Test.COM",
        data={"email": "User@Test.COM", "password": "pw"},
        description="Webasto ThermoConnect - User@Test.COM",
    )


@pytest.mark.asyncio
async def test_user_step_aborts_for_configured_unique_id(monkeypatch) -> None:
    """An account with the same normalized unique ID is not added twice."""
    init_mock = _fake_webasto(monkeypatch)
    flow = _user_flow(existing_unique_ids={"user@test.com"})

    with pytest.raises(AbortFlow) as exc_info:
        await flow.async_step_user({"email": "USER@test.com", "password": "pw"})

    assert exc_info.value.reason == "already_configured"
    init_mock.assert_not_called()


@pytest.mark.asyncio
async def test_user_step_aborts_for_legacy_entry_with_other_case(monkeypatch) -> None:
    """Older entries keep the email as typed as unique ID and still match."""
    init_mock = _fake_webasto(monkeypatch)
    flow = _user_flow(
        existing_unique_ids={"User@Test.com"}, existing_emails=["User@Test.com"]
    )

    result = await flow.async_step_user({"email": "user@test.com", "password": "pw"})

    assert result == {"type": "abort", "reason": "already_configured"}
    init_mock.assert_not_called()
    flow.async_create_entry.assert_not_called()


def test_config_flow_has_no_connection_class() -> None:
    """The obsolete CONNECTION_CLASS attribute is gone."""
    assert "CONNECTION_CLASS" not in WebastoConnectConfigFlow.__dict__
