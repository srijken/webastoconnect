"""Config flows with the real flow manager and config entries."""

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pywebasto.exceptions import (
    InvalidResponseException,
    TooManyRequestsException,
    UnauthorizedException,
)

from custom_components.webastoconnect.const import DOMAIN

from .fake_webasto import EMAIL, PASSWORD, FakeWebastoCloud

USER_INPUT = {CONF_EMAIL: f"  {EMAIL} ", CONF_PASSWORD: PASSWORD}


async def test_user_flow_creates_entry(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud
) -> None:
    """A valid login creates an entry with the lowercase email as unique ID."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    entry = result["result"]
    assert entry.unique_id == EMAIL.lower()
    assert (entry.version, entry.minor_version) == (1, 3)
    # The validation client is closed; the entry is set up with its own client.
    assert fake_cloud.clients[0].closed
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (UnauthorizedException("bad password"), "invalid_auth"),
        (InvalidResponseException("bad gateway"), "cannot_connect"),
        (TooManyRequestsException("slow down"), "ratelimit"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    error: Exception,
    expected: str,
) -> None:
    """Login errors show on the form, and the form can be submitted again."""
    fake_cloud.connect_error = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected}
    assert fake_cloud.clients[-1].closed

    fake_cloud.connect_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_wrong_password(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud
) -> None:
    """A password the cloud rejects shows invalid_auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("unique_id", "stored_email"),
    [
        # Current entries: unique ID is the lowercase email.
        (EMAIL.lower(), EMAIL),
        # Entries from before the lowercase unique ID: email as typed.
        (EMAIL, EMAIL),
    ],
)
async def test_user_flow_duplicate_aborts(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    unique_id: str,
    stored_email: str,
) -> None:
    """The same account, in any letter case, cannot be added twice."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        data={CONF_EMAIL: stored_email, CONF_PASSWORD: PASSWORD},
        version=1,
        minor_version=3,
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL.upper(), CONF_PASSWORD: PASSWORD},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # No login was attempted for a duplicate.
    assert fake_cloud.clients == []
