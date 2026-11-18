"""Serve the bundled Webasto Connect card and clean up older card installs.

The card is served from the integration package and loaded on every
dashboard through the frontend's extra module URLs, so no Lovelace resource
and no copy in `/config/www` are needed. Versions up to 1.4.1 copied the card
to `/config/www/webastoconnect/` and registered a Lovelace resource for it;
`async_remove_legacy_card` removes both.
"""

from hashlib import sha256
import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CARD_FILENAME, CARD_SOURCE_DIR, DOMAIN

LOGGER = logging.getLogger(__name__)

CARD_PATH = Path(__file__).parent / CARD_SOURCE_DIR / CARD_FILENAME
CARD_URL_PATH = f"/{DOMAIN}/{CARD_FILENAME}"
DATA_CARD_REGISTERED = f"{DOMAIN}_card_registered"

# What versions up to 1.4.1 wrote: /config/www/webastoconnect/<card file>,
# served as /local/webastoconnect/<card file>(?v=<hash>).
LEGACY_WWW_SUBDIR = "webastoconnect"
LEGACY_RESOURCE_URL = f"/local/{LEGACY_WWW_SUBDIR}/{CARD_FILENAME}"
CARD_VERSION_MARKER = b"__WEBASTO_CONNECT_CARD_VERSION__"


def read_card_hash(path: Path) -> str | None:
    """Return a short content hash of the card for cache-busting."""
    try:
        return sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled card and load it on every dashboard, once per start."""
    if hass.data.get(DATA_CARD_REGISTERED):
        return
    hass.data[DATA_CARD_REGISTERED] = True

    card_hash = await hass.async_add_executor_job(read_card_hash, CARD_PATH)
    if card_hash is None:
        LOGGER.warning(
            "Webasto Connect Card not found in the integration package at %s",
            CARD_PATH,
        )
        return

    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(CARD_PATH), cache_headers=True)]
    )
    frontend.add_extra_js_url(hass, f"{CARD_URL_PATH}?v={card_hash}")
    LOGGER.debug("Serving Webasto Connect Card at %s?v=%s", CARD_URL_PATH, card_hash)


def is_legacy_resource_url(url: str | None) -> bool:
    """Return whether a Lovelace resource URL is the one older versions created."""
    return bool(url) and url.split("?", 1)[0] == LEGACY_RESOURCE_URL


def remove_legacy_card_file(www_path: Path) -> bool:
    """Delete the card copy older versions wrote to /config/www/webastoconnect.

    Only the card file itself is deleted, and only when it carries the card's
    version marker. The directory is removed only when it is then empty.
    """
    legacy_dir = www_path / LEGACY_WWW_SUBDIR
    legacy_file = legacy_dir / CARD_FILENAME
    try:
        if not legacy_file.is_file():
            return False
        if CARD_VERSION_MARKER not in legacy_file.read_bytes():
            LOGGER.debug("%s is not a Webasto Connect Card copy; kept", legacy_file)
            return False
        legacy_file.unlink()
    except OSError as err:
        LOGGER.warning("Could not remove old card copy %s: %s", legacy_file, err)
        return False

    try:
        if not any(legacy_dir.iterdir()):
            legacy_dir.rmdir()
    except OSError as err:
        LOGGER.debug("Kept directory %s: %s", legacy_dir, err)
    return True


async def _async_remove_legacy_resource(hass: HomeAssistant) -> None:
    """Remove the Lovelace resource older versions created for the card."""
    if (lovelace_data := hass.data.get(LOVELACE_DATA)) is None:
        return
    resources = lovelace_data.resources

    if lovelace_data.resource_mode != MODE_STORAGE:
        # YAML resources are written by the user; only point them out.
        if any(
            is_legacy_resource_url(resource.get(CONF_URL))
            for resource in resources.async_items() or []
        ):
            LOGGER.warning(
                "The Webasto Connect Card now loads automatically; remove the "
                "Lovelace resource %s from your YAML configuration",
                LEGACY_RESOURCE_URL,
            )
        return

    # Loads the stored resources; they are loaded lazily by Lovelace.
    await resources.async_get_info()
    for resource in list(resources.async_items() or []):
        url = resource.get(CONF_URL)
        if not is_legacy_resource_url(url):
            continue
        await resources.async_delete_item(resource["id"])
        LOGGER.info(
            "Removed Lovelace resource %s: the Webasto Connect Card now loads from %s",
            url,
            CARD_URL_PATH,
        )


async def async_remove_legacy_card(hass: HomeAssistant) -> None:
    """Remove the Lovelace resource and the www copy older versions created."""
    try:
        await _async_remove_legacy_resource(hass)
    except HomeAssistantError as err:
        LOGGER.warning("Could not remove the old Lovelace card resource: %s", err)
    www_path = Path(hass.config.path("www"))
    if await hass.async_add_executor_job(remove_legacy_card_file, www_path):
        LOGGER.info(
            "Removed old Webasto Connect Card copy from %s",
            www_path / LEGACY_WWW_SUBDIR,
        )
