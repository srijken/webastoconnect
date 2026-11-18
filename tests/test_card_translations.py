"""Tests for the bundled card's translations."""

import json
from pathlib import Path

import pytest

from custom_components.webastoconnect.card_frontend import CARD_PATH

CARD_TRANSLATIONS = Path(__file__).parents[1] / "card-src" / "translations"
LANGUAGES = ("cs", "da", "de", "en", "es", "fi", "fr", "nb", "nl")
TIMER_LINE_KEYS = {
    "timer_line_heater",
    "timer_line_ventilation",
    "timers_showing_heater",
    "timers_showing_ventilation",
}


def _card_ui(language: str) -> dict[str, str]:
    path = CARD_TRANSLATIONS / f"{language}.json"
    return json.loads(path.read_text("utf-8"))["card"]["ui"]


def test_all_card_languages_are_tested() -> None:
    """Every translation file of the card is covered by these tests."""
    assert {path.stem for path in CARD_TRANSLATIONS.glob("*.json")} == set(LANGUAGES)


@pytest.mark.parametrize("language", LANGUAGES)
def test_card_translation_keys_match_english(language: str) -> None:
    """Every card language has exactly the English keys, all non-empty."""
    ui = _card_ui(language)
    assert set(ui) == set(_card_ui("en"))
    assert all(isinstance(value, str) and value for value in ui.values())


@pytest.mark.parametrize("language", LANGUAGES)
def test_timer_line_card_translations_exist(language: str) -> None:
    """The heater / ventilation timer strings exist; the old note is gone."""
    ui = _card_ui(language)
    assert set(ui) >= TIMER_LINE_KEYS
    assert all(ui[key] for key in TIMER_LINE_KEYS)
    assert ui["timer_line_heater"] != ui["timer_line_ventilation"]
    assert ui["timers_showing_heater"] != ui["timers_showing_ventilation"]
    assert "timers_manage_note" not in ui


def test_bundled_card_contains_timer_line_strings() -> None:
    """The committed card bundle was rebuilt with the new strings."""
    content = CARD_PATH.read_text(encoding="utf-8")
    for key in TIMER_LINE_KEYS:
        assert key in content
    assert "timers_manage_note" not in content
