"""Repair issues for a Webasto ThermoConnect subscription that expires.

The subscription does not renew automatically. It is renewed on My Webasto
Connect; once it lapses, the service is suspended and a reactivation fee
applies. A repair issue per device warns ahead of the expiration date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .api import WebastoConnectUpdateCoordinator
from .base import webasto_device_name
from .const import DOMAIN

LOGGER = logging.getLogger(__name__)

# Days before the expiration date from which the repair issue is shown.
SUBSCRIPTION_EXPIRY_WARNING_DAYS = 30
SUBSCRIPTION_ISSUE_PREFIX = "subscription_expiring_"
SUBSCRIPTION_RENEW_URL = "https://my.webastoconnect.com"


def subscription_issue_id(device_id: Any) -> str:
    """Return the repair issue ID for the subscription of a device."""
    return f"{SUBSCRIPTION_ISSUE_PREFIX}{device_id}"


def subscription_days_left(expiration: Any, now: datetime | None = None) -> int | None:
    """Return the days from today until a subscription expires.

    Days are counted in the local time zone of Home Assistant, from today to
    the local date of the expiration: 0 on the last day, negative once it has
    expired. None when the expiration is unknown. The repair issue and the
    days-left sensor both use this, so they always agree.
    """
    if not isinstance(expiration, datetime):
        return None
    today = dt_util.as_local(now or dt_util.utcnow()).date()
    return (dt_util.as_local(expiration).date() - today).days


@dataclass(frozen=True, slots=True)
class SubscriptionIssue:
    """The repair issue a device's subscription needs."""

    severity: ir.IssueSeverity
    translation_key: str
    placeholders: tuple[tuple[str, str], ...]


def subscription_issue(
    expiration: Any, device_name: str, now: datetime | None = None
) -> SubscriptionIssue | None:
    """Return the issue for a subscription expiration, or None if not needed.

    Days are counted as in `subscription_days_left`.
    """
    days_left = subscription_days_left(expiration, now)
    if days_left is None or days_left > SUBSCRIPTION_EXPIRY_WARNING_DAYS:
        return None
    expiration_date = dt_util.as_local(expiration).date()
    if days_left < 0:
        return SubscriptionIssue(
            severity=ir.IssueSeverity.ERROR,
            translation_key="subscription_expired",
            placeholders=(
                ("device", device_name),
                ("expiration_date", expiration_date.isoformat()),
            ),
        )
    return SubscriptionIssue(
        severity=ir.IssueSeverity.WARNING,
        translation_key="subscription_expiring",
        placeholders=(
            ("device", device_name),
            ("expiration_date", expiration_date.isoformat()),
            ("days_left", str(days_left)),
        ),
    )


class SubscriptionIssueTracker:
    """Keep the subscription repair issues in line with the cloud data.

    The last issue per device is kept in memory, so the issue registry is only
    changed when an issue appears, changes or goes away, not on every poll.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: WebastoConnectUpdateCoordinator,
    ) -> None:
        """Initialize the tracker."""
        self._hass = hass
        self._entry = entry
        self._entry_id = entry.entry_id
        self._coordinator = coordinator
        # Device ID -> the issue last created (None: no issue).
        self._issues: dict[str, SubscriptionIssue | None] = {}

    @callback
    def async_start(self) -> None:
        """Follow coordinator updates until the entry unloads."""
        self._entry.async_on_unload(
            self._coordinator.async_add_listener(self._async_handle_update)
        )
        self._entry.async_on_unload(self.async_delete_all)

    @callback
    def _async_handle_update(self) -> None:
        """Update the issues after a successful coordinator update."""
        if self._coordinator.last_update_success:
            self.async_update()

    @callback
    def async_update(self) -> None:
        """Create, update or delete the issue of every device."""
        current: set[str] = set()
        for device_id, device in self._coordinator.cloud.devices.items():
            key = str(device_id)
            current.add(key)
            device_name = webasto_device_name(
                device, self._coordinator.device_names.get(key)
            )
            issue = subscription_issue(
                getattr(device, "subscription_expiration", None), device_name
            )
            # A device seen for the first time is always written, so an issue
            # left from before a reload is replaced or deleted.
            if key in self._issues and self._issues[key] == issue:
                continue
            self._issues[key] = issue
            self._async_write(key, issue)

        for key in set(self._issues) - current:
            LOGGER.debug("Device %s no longer reported; removing its issue", key)
            del self._issues[key]
            ir.async_delete_issue(self._hass, DOMAIN, subscription_issue_id(key))

    @callback
    def _async_write(self, device_id: str, issue: SubscriptionIssue | None) -> None:
        """Write one device's issue to the issue registry."""
        issue_id = subscription_issue_id(device_id)
        if issue is None:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id,
            data={"entry_id": self._entry_id},
            is_fixable=False,
            learn_more_url=SUBSCRIPTION_RENEW_URL,
            severity=issue.severity,
            translation_key=issue.translation_key,
            translation_placeholders=dict(issue.placeholders),
        )

    @callback
    def async_delete_all(self) -> None:
        """Delete the issues of this entry's devices."""
        for key in self._issues:
            ir.async_delete_issue(self._hass, DOMAIN, subscription_issue_id(key))
        self._issues.clear()


@callback
def async_delete_subscription_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete every subscription issue of a config entry.

    Covers issues created by the entry and issues of the entry's devices in
    the device registry, also ones that are inactive or were dismissed.
    """
    issue_ids = {
        issue_id
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
        and issue_id.startswith(SUBSCRIPTION_ISSUE_PREFIX)
        and (issue.data or {}).get("entry_id") == entry.entry_id
    }
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        issue_ids.update(
            subscription_issue_id(identifier)
            for domain, identifier in device.identifiers
            if domain == DOMAIN
        )
    for issue_id in issue_ids:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
