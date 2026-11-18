"""Tests for the app credential Store, its migration and its removal."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

import custom_components.webastoconnect as integration
from custom_components.webastoconnect.api import (
    _credential_callbacks,
    _credential_store_key,
)

CREDENTIALS = {"client_id": "client", "client_secret": "secret"}


@pytest_asyncio.fixture
async def hass(tmp_path: Path):
    """Return a minimal Home Assistant instance with a temporary config dir."""
    instance = HomeAssistant(str(tmp_path))
    yield instance
    await instance.async_stop(force=True)


@pytest.fixture
def entry() -> SimpleNamespace:
    """Return a config entry stand-in."""
    return SimpleNamespace(entry_id="entry-1")


def _legacy_path(tmp_path: Path) -> Path:
    return tmp_path / ".storage" / "webasto_entry-1.json"


def _store_path(tmp_path: Path) -> Path:
    return tmp_path / ".storage" / "webastoconnect.credentials.entry-1"


def _write_legacy(tmp_path: Path, data: dict[str, str]) -> Path:
    path = _legacy_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def test_store_key_is_per_entry(entry) -> None:
    """Each config entry has its own credential Store."""
    assert _credential_store_key(entry) == "webastoconnect.credentials.entry-1"


@pytest.mark.asyncio
async def test_callbacks_save_and_load_through_private_store(
    hass, entry, tmp_path: Path
) -> None:
    """Saved credentials end up in a versioned, owner-only Store file."""
    credential_load, credential_save = _credential_callbacks(hass, entry)

    assert await credential_load() is None
    assert not _store_path(tmp_path).exists()

    await credential_save(SimpleNamespace(**CREDENTIALS))

    assert await credential_load() == CREDENTIALS
    path = _store_path(tmp_path)
    assert os.stat(path).st_mode & 0o777 == 0o600
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["version"] == 1
    assert stored["key"] == "webastoconnect.credentials.entry-1"
    assert stored["data"] == CREDENTIALS
    assert not _legacy_path(tmp_path).exists()


@pytest.mark.asyncio
async def test_legacy_file_is_moved_into_store(hass, entry, tmp_path: Path) -> None:
    """Credentials from the old JSON file are kept and moved to the Store."""
    legacy = _write_legacy(tmp_path, CREDENTIALS)
    credential_load, _ = _credential_callbacks(hass, entry)

    assert await credential_load() == CREDENTIALS

    assert not legacy.exists()
    stored = json.loads(_store_path(tmp_path).read_text(encoding="utf-8"))
    assert stored["data"] == CREDENTIALS

    # A new client (e.g. after a restart) reads them from the Store.
    credential_load, _ = _credential_callbacks(hass, entry)
    assert await credential_load() == CREDENTIALS


@pytest.mark.asyncio
async def test_store_data_wins_over_legacy_file(hass, entry, tmp_path: Path) -> None:
    """When the Store has data, the old file is not read."""
    await Store(hass, 1, "webastoconnect.credentials.entry-1").async_save(CREDENTIALS)
    legacy = _write_legacy(tmp_path, {"client_id": "old", "client_secret": "old"})
    credential_load, _ = _credential_callbacks(hass, entry)

    assert await credential_load() == CREDENTIALS
    assert legacy.exists()


@pytest.mark.asyncio
async def test_legacy_file_is_kept_when_store_save_fails(
    hass, entry, tmp_path: Path, monkeypatch
) -> None:
    """The old file is only deleted after the Store has saved the data."""
    legacy = _write_legacy(tmp_path, CREDENTIALS)

    async def failing_save(self, data) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Store, "async_save", failing_save)
    credential_load, _ = _credential_callbacks(hass, entry)

    with pytest.raises(OSError):
        await credential_load()

    assert legacy.exists()


@pytest.mark.asyncio
async def test_remove_entry_deletes_store_and_legacy_file(
    hass, entry, tmp_path: Path
) -> None:
    """Removing the entry deletes the Store data and any leftover old file."""
    _, credential_save = _credential_callbacks(hass, entry)
    await credential_save(SimpleNamespace(**CREDENTIALS))
    legacy = _write_legacy(tmp_path, CREDENTIALS)

    await integration.async_remove_entry(hass, entry)

    assert not _store_path(tmp_path).exists()
    assert not legacy.exists()
    credential_load, _ = _credential_callbacks(hass, entry)
    assert await credential_load() is None

    # Removing again (nothing left) does not fail.
    await integration.async_remove_entry(hass, entry)
