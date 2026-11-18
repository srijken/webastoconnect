"""Tests for serving the bundled card and removing older card installs."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import (
    LOVELACE_DATA,
    MODE_STORAGE,
    MODE_YAML,
)
from homeassistant.const import CONF_TYPE, CONF_URL
from homeassistant.exceptions import HomeAssistantError

import custom_components.webastoconnect as integration
from custom_components.webastoconnect import card_frontend
from custom_components.webastoconnect.card_frontend import (
    CARD_PATH,
    CARD_URL_PATH,
    async_register_card,
    async_remove_legacy_card,
    is_legacy_resource_url,
    read_card_hash,
    remove_legacy_card_file,
)

OLD_URL = "/local/webastoconnect/webasto-connect-card.js"
CARD_JS = 'globalThis.__WEBASTO_CONNECT_CARD_VERSION__ = "0.1.0";\n'


class FakeResources:
    """Lovelace storage collection stand-in that loads lazily like the real one."""

    def __init__(self, items: list[dict]) -> None:
        """Store the items; they become visible once loaded."""
        self._stored = {item["id"]: item for item in items}
        self.loaded = False
        self.deleted: list[str] = []

    async def async_get_info(self) -> dict[str, int]:
        """Load the items."""
        self.loaded = True
        return {"resources": len(self._stored)}

    def async_items(self) -> list[dict]:
        """Return the loaded items."""
        return list(self._stored.values()) if self.loaded else []

    async def async_delete_item(self, item_id: str) -> None:
        """Delete an item."""
        assert self.loaded
        del self._stored[item_id]
        self.deleted.append(item_id)


def _hass(tmp_path: Path, lovelace=None) -> SimpleNamespace:
    async def async_add_executor_job(func, *args):
        return func(*args)

    data = {}
    if lovelace is not None:
        data[LOVELACE_DATA] = lovelace
    return SimpleNamespace(
        data=data,
        http=SimpleNamespace(async_register_static_paths=AsyncMock()),
        async_add_executor_job=AsyncMock(side_effect=async_add_executor_job),
        config=SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts))),
    )


def _write_legacy_file(tmp_path: Path, content: str = CARD_JS) -> Path:
    legacy_dir = tmp_path / "www" / "webastoconnect"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "webasto-connect-card.js"
    legacy_file.write_text(content, encoding="utf-8")
    return legacy_file


def test_bundled_card_exists_and_guards_element_definitions() -> None:
    """The served file exists and does not redefine elements when loaded twice."""
    content = CARD_PATH.read_text(encoding="utf-8")
    assert 'customElements.get("webasto-connect-card")' in content
    assert 'customElements.get("webasto-connect-card-editor")' in content
    assert "window.customCards.some(" in content


def test_read_card_hash(tmp_path: Path) -> None:
    """The hash is short and missing files give None."""
    card = tmp_path / "card.js"
    card.write_text("console.log('webasto');", encoding="utf-8")
    assert len(read_card_hash(card)) == 12
    assert read_card_hash(tmp_path / "missing.js") is None


@pytest.mark.asyncio
async def test_register_card_serves_file_and_adds_versioned_url(
    tmp_path: Path, monkeypatch
) -> None:
    """The card is served from the package and loaded with a version query."""
    add_url = Mock()
    monkeypatch.setattr(card_frontend.frontend, "add_extra_js_url", add_url)
    hass = _hass(tmp_path)

    await async_register_card(hass)
    await async_register_card(hass)

    hass.http.async_register_static_paths.assert_awaited_once_with(
        [StaticPathConfig(CARD_URL_PATH, str(CARD_PATH), cache_headers=True)]
    )
    assert CARD_URL_PATH == "/webastoconnect/webasto-connect-card.js"
    add_url.assert_called_once_with(
        hass, f"{CARD_URL_PATH}?v={read_card_hash(CARD_PATH)}"
    )
    # Hashing ran in the executor.
    hass.async_add_executor_job.assert_awaited_once_with(read_card_hash, CARD_PATH)


@pytest.mark.asyncio
async def test_register_card_skips_when_bundle_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """Without a bundled file nothing is registered."""
    add_url = Mock()
    monkeypatch.setattr(card_frontend.frontend, "add_extra_js_url", add_url)
    monkeypatch.setattr(card_frontend, "CARD_PATH", tmp_path / "missing.js")
    hass = _hass(tmp_path)

    await async_register_card(hass)

    hass.http.async_register_static_paths.assert_not_awaited()
    add_url.assert_not_called()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (OLD_URL, True),
        (f"{OLD_URL}?v=abc123def456", True),
        ("/local/webastoconnect/other.js", False),
        ("/local/other/webasto-connect-card.js", False),
        ("/hacsfiles/webasto-connect-card.js", False),
        ("", False),
        (None, False),
    ],
)
def test_is_legacy_resource_url(url, expected) -> None:
    """Only the URL older versions created matches."""
    assert is_legacy_resource_url(url) is expected


@pytest.mark.asyncio
async def test_remove_legacy_card_removes_own_resource_and_file(
    tmp_path: Path,
) -> None:
    """The old resource and copy go; other resources and files stay."""
    resources = FakeResources(
        [
            {"id": "old", CONF_URL: f"{OLD_URL}?v=oldhash", CONF_TYPE: "module"},
            {"id": "old-plain", CONF_URL: OLD_URL, CONF_TYPE: "module"},
            {"id": "other", CONF_URL: "/hacsfiles/x/x.js", CONF_TYPE: "module"},
            {
                "id": "other-local",
                CONF_URL: "/local/webastoconnect/mine.js",
                CONF_TYPE: "module",
            },
        ]
    )
    lovelace = SimpleNamespace(resource_mode=MODE_STORAGE, resources=resources)
    legacy_file = _write_legacy_file(tmp_path)
    hass = _hass(tmp_path, lovelace)

    await async_remove_legacy_card(hass)

    assert resources.deleted == ["old", "old-plain"]
    assert [item["id"] for item in resources.async_items()] == [
        "other",
        "other-local",
    ]
    assert not legacy_file.exists()
    assert not legacy_file.parent.exists()


@pytest.mark.asyncio
async def test_remove_legacy_card_is_noop_when_nothing_exists(
    tmp_path: Path,
) -> None:
    """Without an old resource or copy nothing changes."""
    resources = FakeResources(
        [{"id": "other", CONF_URL: "/hacsfiles/x/x.js", CONF_TYPE: "module"}]
    )
    lovelace = SimpleNamespace(resource_mode=MODE_STORAGE, resources=resources)
    hass = _hass(tmp_path, lovelace)

    await async_remove_legacy_card(hass)
    await async_remove_legacy_card(_hass(tmp_path))

    assert resources.deleted == []
    assert not (tmp_path / "www").exists()


@pytest.mark.asyncio
async def test_remove_legacy_card_leaves_yaml_resources(tmp_path: Path) -> None:
    """YAML resources are only reported, never changed."""
    resources = SimpleNamespace(
        async_items=lambda: [{CONF_URL: OLD_URL, CONF_TYPE: "module"}],
        async_get_info=AsyncMock(),
        async_delete_item=AsyncMock(),
    )
    lovelace = SimpleNamespace(resource_mode=MODE_YAML, resources=resources)

    await async_remove_legacy_card(_hass(tmp_path, lovelace))

    resources.async_get_info.assert_not_awaited()
    resources.async_delete_item.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_legacy_card_still_removes_file_when_storage_fails(
    tmp_path: Path,
) -> None:
    """A Lovelace storage error is logged and does not stop the cleanup."""
    resources = SimpleNamespace(
        async_get_info=AsyncMock(side_effect=HomeAssistantError("boom")),
    )
    lovelace = SimpleNamespace(resource_mode=MODE_STORAGE, resources=resources)
    legacy_file = _write_legacy_file(tmp_path)

    await async_remove_legacy_card(_hass(tmp_path, lovelace))

    assert not legacy_file.exists()


def test_remove_legacy_file_keeps_other_files(tmp_path: Path) -> None:
    """The directory stays when the user put other files in it."""
    legacy_file = _write_legacy_file(tmp_path)
    other = legacy_file.parent / "mine.js"
    other.write_text("x", encoding="utf-8")

    assert remove_legacy_card_file(tmp_path / "www") is True

    assert not legacy_file.exists()
    assert other.exists()


def test_remove_legacy_file_keeps_unrelated_file(tmp_path: Path) -> None:
    """A file with the same name but other content is not deleted."""
    legacy_file = _write_legacy_file(tmp_path, "console.log('not the card');")

    assert remove_legacy_card_file(tmp_path / "www") is False

    assert legacy_file.exists()


@pytest.mark.asyncio
async def test_async_setup_registers_card_and_cleans_up(monkeypatch) -> None:
    """async_setup serves the card and removes the old install."""
    register = AsyncMock()
    cleanup = AsyncMock()
    monkeypatch.setattr(integration, "async_register_card", register)
    monkeypatch.setattr(integration, "async_remove_legacy_card", cleanup)
    monkeypatch.setattr(integration, "async_setup_services", Mock())
    hass = SimpleNamespace()

    assert await integration.async_setup(hass, {}) is True

    register.assert_awaited_once_with(hass)
    cleanup.assert_awaited_once_with(hass)


def test_manifest_depends_on_http_and_frontend() -> None:
    """The static path and extra module URL need http and frontend set up first."""
    import json

    manifest = json.loads(
        (Path(integration.__file__).parent / "manifest.json").read_text("utf-8")
    )
    assert {"http", "frontend"} <= set(manifest["dependencies"])
