"""Fixtures for the tests that run against a real Home Assistant core."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.webastoconnect.const import DOMAIN

from .fake_webasto import EMAIL, PASSWORD, FakeWebastoCloud

# Dependencies of the integration (manifest.json). Setting them up needs the
# frontend package; the bundled card is covered by tests/test_card_frontend.py.
FRONTEND_DEPENDENCIES = ("http", "frontend", "lovelace")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components/."""


@pytest.fixture(autouse=True)
def skip_frontend(hass: HomeAssistant) -> Generator[None]:
    """Mark the frontend dependencies as set up and skip serving the card."""
    hass.config.components.update(FRONTEND_DEPENDENCIES)
    with (
        patch("custom_components.webastoconnect.async_register_card", AsyncMock()),
        patch("custom_components.webastoconnect.async_remove_legacy_card", AsyncMock()),
    ):
        yield


@pytest.fixture
def fake_cloud() -> Generator[FakeWebastoCloud]:
    """Replace WebastoConnect where the integration imports it."""
    cloud = FakeWebastoCloud()
    with (
        patch("custom_components.webastoconnect.api.WebastoConnect", cloud.client),
        patch(
            "custom_components.webastoconnect.config_flow.WebastoConnect",
            cloud.client,
        ),
    ):
        yield cloud


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry at the current version, added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL.lower(),
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        version=1,
        minor_version=3,
    )
    entry.add_to_hass(hass)
    return entry
