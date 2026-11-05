"""Sprache der Oberfläche. Die deutschen Texte stehen im Code und dienen als Schlüssel, andere Sprachen liegen in lang/<code>.json.

Die Sprache steht fest, sobald dieses Modul geladen ist, damit auch Konstanten der anderen Module übersetzt sind.
Ein Wechsel wird deshalb erst nach einem Neustart wirksam. Fehlt eine Übersetzung, erscheint der deutsche Text.
"""

import ctypes
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("llama_starter_config.json")
LANG_DIR = Path(__file__).with_name("lang")
SOURCE_LANGUAGE = "de"
SOURCE_NAME = "Deutsch"
FALLBACK_LANGUAGE = "en"  # Für Windows-Sprachen, zu denen es keine Datei gibt.
WINDOWS_GERMAN = 0x07  # Die unteren 10 Bit der Windows-Sprachkennung nennen die Sprache, der Rest die Region.

current = SOURCE_LANGUAGE
_strings = {}
_thousands = "."
_decimal = ","


def _read_file(code):
    """Inhalt einer Sprachdatei oder None, wenn sie fehlt oder kaputt ist."""
    try:
        data = json.loads((LANG_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("strings"), dict) else None


def available():
    """Alle wählbaren Sprachen als {Code: Name}. Eine neue Datei in lang/ erscheint von selbst."""
    languages = {SOURCE_LANGUAGE: SOURCE_NAME}
    for path in sorted(LANG_DIR.glob("*.json")):
        data = _read_file(path.stem)
        if data:
            languages[path.stem] = data.get("language", path.stem)
    return languages


def set_language(code):
    """Lädt eine Sprache. Unbekannte Codes und kaputte Dateien führen zum deutschen Original."""
    global current, _strings, _thousands, _decimal
    data = None if code == SOURCE_LANGUAGE else _read_file(code)
    current = code if data else SOURCE_LANGUAGE
    _strings = data["strings"] if data else {}
    _thousands = data.get("thousands", ".") if data else "."
    _decimal = data.get("decimal", ",") if data else ","


def tr(source, /, **values):
    """Übersetzt einen deutschen Text. Platzhalter wie {name} werden aus values gefüllt."""
    text = _strings.get(source, source)
    if not values:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):  # Eine fehlerhafte Übersetzung darf das Programm nicht stoppen.
        return source.format(**values)


def tr_in(code, source):
    """Übersetzt in eine andere als die aktuelle Sprache, für die Rückfrage beim Sprachwechsel."""
    data = None if code == SOURCE_LANGUAGE else _read_file(code)
    return data["strings"].get(source, source) if data else source


def number_text(text):
    """Setzt eine Zahl aus Pythons Schreibweise 1,234.5 in die Schreibweise der aktuellen Sprache."""
    return text.replace(",", "\0").replace(".", _decimal).replace("\0", _thousands)


def _configured_language():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("language")
    except (OSError, ValueError, AttributeError):
        return None


def _system_language():
    try:
        windows = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
    except (AttributeError, OSError):
        return FALLBACK_LANGUAGE
    return SOURCE_LANGUAGE if windows == WINDOWS_GERMAN else FALLBACK_LANGUAGE


set_language(_configured_language() or _system_language())
