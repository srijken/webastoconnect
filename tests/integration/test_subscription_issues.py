"""Subscription expiry repair issues with a real Home Assistant core."""

from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pywebasto.exceptions import InvalidResponseException

from custom_components.webastoconnect import subscription
from custom_components.webastoconnect.api import SCAN_INTERVAL
from custom_components.webastoconnect.const import DOMAIN
from custom_components.webastoconnect.subscription import (
    SUBSCRIPTION_EXPIRY_WARNING_DAYS,
    SubscriptionIssueTracker,
)

from .fake_webasto import DEVICE_ID, DEVICE_NAME, FakeWebastoCloud

ISSUE_ID = f"subscription_expiring_{DEVICE_ID}"
NOW = "2026-11-18 12:00:00+00:00"

pytestmark = pytest.mark.freeze_time(NOW)


def _expires_in(days: int) -> int:
    """Return an expiration timestamp at local noon, `days` from today."""
    day = dt_util.now().date() + timedelta(days=days)
    local = datetime.combine(day, time(12), tzinfo=dt_util.get_default_time_zone())
    return int(local.timestamp())


def _set_expiration(cloud: FakeWebastoCloud, timestamp: int) -> None:
    cloud.devices[DEVICE_ID]["subscription"]["expiration"] = timestamp


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _calls(mock, issue_id: str = ISSUE_ID) -> int:
    """Count registry calls for one of our issues (`ir` is shared with HA)."""
    return sum(
        1 for call in mock.call_args_list if call.args[1:3] == (DOMAIN, issue_id)
    )


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)


async def test_real_expiration_date_warns(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """The real expiration date (2026-12-09) warns with 21 days left."""
    await _setup(hass, config_entry)

    issue = _issue(hass)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "subscription_expiring"
    assert issue.translation_placeholders == {
        "device": DEVICE_NAME,
        "expiration_date": "2026-12-09",
        "days_left": "21",
    }
    assert issue.is_fixable is False
    assert issue.learn_more_url == "https://my.webastoconnect.com"
    assert issue.data == {"entry_id": config_entry.entry_id}


@pytest.mark.parametrize(
    "days", [SUBSCRIPTION_EXPIRY_WARNING_DAYS + 1, 365], ids=["31_days", "a_year"]
)
async def test_no_issue_beyond_threshold(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    days: int,
) -> None:
    """More than 30 days left: no issue."""
    _set_expiration(fake_cloud, _expires_in(days))
    await _setup(hass, config_entry)

    assert _issue(hass) is None


@pytest.mark.parametrize("days", [30, 3, 0])
async def test_warning_within_threshold(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    days: int,
) -> None:
    """30 days or fewer left (also the last day): a warning."""
    _set_expiration(fake_cloud, _expires_in(days))
    await _setup(hass, config_entry)

    issue = _issue(hass)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "subscription_expiring"
    assert issue.translation_placeholders == {
        "device": DEVICE_NAME,
        "expiration_date": (dt_util.now().date() + timedelta(days=days)).isoformat(),
        "days_left": str(days),
    }
    assert issue.learn_more_url == "https://my.webastoconnect.com"


async def test_expired_is_error(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """An expiration date in the past: an error."""
    _set_expiration(fake_cloud, _expires_in(-1))
    await _setup(hass, config_entry)

    issue = _issue(hass)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "subscription_expired"
    assert issue.translation_placeholders == {
        "device": DEVICE_NAME,
        "expiration_date": "2026-11-17",
    }
    assert issue.learn_more_url == "https://my.webastoconnect.com"


async def test_unknown_expiration_no_issue(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """No subscription data from the cloud: no issue."""
    del fake_cloud.devices[DEVICE_ID]["subscription"]
    await _setup(hass, config_entry)

    assert _issue(hass) is None


async def test_issue_follows_updates(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Warning becomes error when it lapses, and goes away when renewed."""
    _set_expiration(fake_cloud, _expires_in(3))
    await _setup(hass, config_entry)
    assert _issue(hass).severity is ir.IssueSeverity.WARNING

    _set_expiration(fake_cloud, _expires_in(-2))
    await _tick(hass, freezer, SCAN_INTERVAL)
    assert _issue(hass).severity is ir.IssueSeverity.ERROR

    # A failed update changes nothing.
    fake_cloud.request_error = InvalidResponseException("502 Bad Gateway")
    _set_expiration(fake_cloud, _expires_in(365))
    await _tick(hass, freezer, SCAN_INTERVAL)
    assert _issue(hass).severity is ir.IssueSeverity.ERROR

    # Renewed: the next successful update deletes the issue.
    fake_cloud.request_error = None
    await _tick(hass, freezer, timedelta(seconds=300))
    assert _issue(hass) is None


async def test_unchanged_polls_do_not_recreate(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Polls with the same result do not touch the issue registry."""
    create = ir.async_create_issue
    delete = ir.async_delete_issue
    with (
        patch.object(subscription.ir, "async_create_issue", wraps=create) as created,
        patch.object(subscription.ir, "async_delete_issue", wraps=delete) as deleted,
    ):
        await _setup(hass, config_entry)
        assert _calls(created) == 1
        for _ in range(5):
            await _tick(hass, freezer, SCAN_INTERVAL)
        assert _calls(created) == 1

        # A new day changes the days left, so the issue is updated once.
        await _tick(hass, freezer, timedelta(days=1))
        assert _calls(created) == 2
        assert _issue(hass).translation_placeholders["days_left"] == "20"
        assert _calls(deleted) == 0


async def test_unchanged_polls_without_issue_do_not_delete(
    hass: HomeAssistant,
    fake_cloud: FakeWebastoCloud,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without an issue, polls do not delete again and again."""
    _set_expiration(fake_cloud, _expires_in(365))
    delete = ir.async_delete_issue
    with patch.object(subscription.ir, "async_delete_issue", wraps=delete) as deleted:
        await _setup(hass, config_entry)
        calls = _calls(deleted)
        for _ in range(5):
            await _tick(hass, freezer, SCAN_INTERVAL)
        assert _calls(deleted) == calls


async def test_unload_and_remove_delete_issue(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """Unload deletes the issue, reload restores it, removal deletes it."""
    await _setup(hass, config_entry)
    assert _issue(hass) is not None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass) is None

    await _setup(hass, config_entry)
    assert _issue(hass) is not None

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass) is None


async def test_remove_deletes_dismissed_and_unloaded_issues(
    hass: HomeAssistant, fake_cloud: FakeWebastoCloud, config_entry: MockConfigEntry
) -> None:
    """Removing a not-loaded entry deletes issues of its devices."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    # An issue left without data, like an inactive one after a restart.
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="subscription_expiring",
    )
    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass) is None


async def test_issue_of_device_no_longer_reported_is_deleted(
    hass: HomeAssistant,
) -> None:
    """A device that disappears from the account loses its issue."""
    soon = dt_util.utcnow() + timedelta(days=5)
    devices = {
        "1": SimpleNamespace(name="One", subscription_expiration=soon),
        "2": SimpleNamespace(name="Two", subscription_expiration=soon),
    }
    entry = MockConfigEntry(domain=DOMAIN)
    coordinator = SimpleNamespace(
        cloud=SimpleNamespace(devices=devices),
        device_names={},
        last_update_success=True,
    )
    tracker = SubscriptionIssueTracker(hass, entry, coordinator)
    registry = ir.async_get(hass)

    tracker.async_update()
    assert registry.async_get_issue(DOMAIN, "subscription_expiring_1") is not None
    assert registry.async_get_issue(DOMAIN, "subscription_expiring_2") is not None

    del devices["2"]
    tracker.async_update()
    assert registry.async_get_issue(DOMAIN, "subscription_expiring_1") is not None
    assert registry.async_get_issue(DOMAIN, "subscription_expiring_2") is None

    tracker.async_delete_all()
    assert registry.async_get_issue(DOMAIN, "subscription_expiring_1") is None
