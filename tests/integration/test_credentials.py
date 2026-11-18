"""Reauth, reconfigure and credential migration with a real Home Assistant core."""

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.webastoconnect.const import DOMAIN

from .fake_webasto import EMAIL, PASSWORD, FakeWebastoCloud

NEW_PASSWORD = "battery-staple"


async def test_reauth_after_failed_setup_loads_entry(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """Completing reauth stores the new password and sets the entry up.

    Regression test: the password was stored, but the entry that had failed
    setup was not reloaded and stayed in SETUP_ERROR.
    """
    fake_cloud.password = NEW_PASSWORD
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR

    [flow] = config_entry.async_get_active_flows(hass, {SOURCE_REAUTH})
    assert flow["step_id"] == "reauth_confirm"

    # The old password is still rejected.
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert config_entry.data[CONF_PASSWORD] == PASSWORD

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSWORD: NEW_PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data == {CONF_EMAIL: EMAIL, CONF_PASSWORD: NEW_PASSWORD}
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.coordinator.cloud.password == NEW_PASSWORD
    assert hass.states.get("switch.defender_heater") is not None


async def test_reauth_on_loaded_entry_reloads(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """A reauth started for a loaded entry also reloads it with the new password."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    old_client = config_entry.runtime_data.coordinator.cloud

    fake_cloud.password = NEW_PASSWORD
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: NEW_PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert config_entry.state is ConfigEntryState.LOADED
    assert old_client.closed
    assert config_entry.runtime_data.coordinator.cloud.password == NEW_PASSWORD


async def test_reconfigure_updates_password_and_reloads(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """Reconfigure validates the new password, stores it and reloads."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    old_client = config_entry.runtime_data.coordinator.cloud

    fake_cloud.password = NEW_PASSWORD
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["description_placeholders"] == {CONF_EMAIL: EMAIL}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "typo"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: NEW_PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_PASSWORD] == NEW_PASSWORD
    assert config_entry.state is ConfigEntryState.LOADED
    assert old_client.closed
    new_client = config_entry.runtime_data.coordinator.cloud
    assert new_client is not old_client
    assert new_client.password == NEW_PASSWORD


async def test_migrate_credentials_from_options(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud
) -> None:
    """Entries 1.1 kept the credentials in the options; they move to the data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={},
        options={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, "other": "kept"},
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (1, 3)
    assert entry.data == {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    assert entry.options == {"other": "kept"}
    assert entry.runtime_data.coordinator.cloud.password == PASSWORD


async def test_migrate_from_future_version_fails(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud
) -> None:
    """An entry from a newer major version is not downgraded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        version=2,
        minor_version=1,
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert fake_cloud.clients == []
