"""Tests for config entry setup and unload."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import custom_components.webastoconnect as integration
import custom_components.webastoconnect.config_flow as config_flow


@pytest.fixture(autouse=True)
def mock_integration_version(monkeypatch) -> None:
    """Provide integration metadata for config flow helpers."""
    monkeypatch.setattr(
        config_flow,
        "async_get_integration",
        AsyncMock(return_value=SimpleNamespace(version="test")),
    )


@pytest.mark.asyncio
async def test_async_setup_entry_stores_coordinator_without_listener(
    monkeypatch,
) -> None:
    """Setup stores the coordinator and registers no update listener."""
    services = SimpleNamespace(
        has_service=Mock(return_value=False),
        async_register=Mock(),
        async_remove=Mock(),
    )
    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
        services=services,
    )
    remove_listener = Mock()
    entry = SimpleNamespace(entry_id="entry-1", add_update_listener=Mock())
    entry.add_update_listener.return_value = remove_listener
    coordinator = SimpleNamespace()

    async def fake_setup(hass_obj, config_entry):
        return coordinator

    monkeypatch.setattr(integration, "_async_setup", fake_setup)

    result = await integration.async_setup_entry(hass, entry)

    assert result is True
    entry.add_update_listener.assert_not_called()
    assert entry.runtime_data.coordinator is coordinator


@pytest.mark.asyncio
async def test_async_unload_entry_closes_cloud_on_success() -> None:
    """Unload should close the cloud session."""
    close_mock = AsyncMock()
    services = SimpleNamespace(
        has_service=Mock(return_value=True),
        async_register=Mock(),
        async_remove=Mock(),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        runtime_data=SimpleNamespace(
            coordinator=SimpleNamespace(cloud=SimpleNamespace(close=close_mock)),
        ),
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_unload_platforms=AsyncMock(return_value=True),
            async_entries=Mock(return_value=[]),
        ),
        services=services,
    )

    unload_ok = await integration.async_unload_entry(hass, entry)

    assert unload_ok is True
    close_mock.assert_awaited_once()
