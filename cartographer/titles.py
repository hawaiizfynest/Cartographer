"""
titles.py - resolve a cartridge's full game title.

Two tiers, in order of confidence:

1. SHA-1 of the full ROM against a No-Intro-derived database (exact release).
   This is what FlashGBX does. It needs a complete dump and a bundled hash DB
   (``titles_sha1.json``), which is optional - if absent, we fall back to:

2. The 4-character game code from the header (``titles_codes.json``), which
   resolves the game family/release instantly from just the header, before any
   dump. Falls back further to the raw 12-char internal title if the code is
   unknown.

The bundled JSON files are optional and can be expanded over time. No copyright
game data is embedded here; the lookup tables map identifiers to human title
strings only.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass


def _data_dir() -> str:
    # Under a PyInstaller one-file build, bundled data lives under sys._MEIPASS.
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "cartographer", "data")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


_DATA_DIR = _data_dir()


def _load(name: str) -> dict:
    path = os.path.join(_DATA_DIR, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_CODES = _load("titles_codes.json")
_SHA1 = _load("titles_sha1.json")


@dataclass
class TitleInfo:
    full_title: str
    source: str          # "sha1", "code", or "header"
    game_code: str = ""
    short_title: str = ""
    save_type: str = ""  # e.g. "eeprom_4k", "flash_1m" (from DB, may be "")
    rom_size: str = ""   # human string from DB, may be ""

    @property
    def is_exact(self) -> bool:
        return self.source == "sha1"


def _code_entry(code: str) -> dict:
    if not code:
        return {}
    entry = _CODES.get(code) or _CODES.get(code.upper())
    if entry is None:
        return {}
    if isinstance(entry, str):        # legacy flat schema: just a title string
        return {"title": entry}
    return entry


def gba_header_fields(header: bytes) -> tuple[str, str]:
    """Return (short_title, game_code) from a GBA header (>= 0xB0 bytes)."""
    if len(header) < 0xB0:
        return "", ""
    short = header[0xA0:0xAC].decode("ascii", "replace").strip("\x00 ")
    code = header[0xAC:0xB0].decode("ascii", "replace").strip("\x00 ")
    return short, code


def resolve_gba(header: bytes, rom: bytes | None = None) -> TitleInfo:
    """Resolve a GBA title. If the full ROM is given, try SHA-1 first."""
    short, code = gba_header_fields(header)
    entry = _code_entry(code)

    if rom:
        digest = hashlib.sha1(rom).hexdigest().lower()
        hit = _SHA1.get(digest)
        if hit:
            if isinstance(hit, dict):
                return TitleInfo(hit.get("title", short), "sha1", code, short,
                                 hit.get("save", entry.get("save", "")),
                                 hit.get("rom", entry.get("rom", "")))
            return TitleInfo(hit, "sha1", code, short,
                             entry.get("save", ""), entry.get("rom", ""))

    if entry:
        return TitleInfo(entry.get("title", short or "(unknown)"), "code", code,
                         short, entry.get("save", ""), entry.get("rom", ""))

    return TitleInfo(short or "(unknown)", "header", code, short)


def save_type_for_code(code: str) -> str:
    """Return the DB save type for a game code, or '' if unknown."""
    return _code_entry(code).get("save", "")


def _user_db_path() -> str:
    """A user-writable location for SHA-1 entries learned from the user's own
    dumps. Kept separate from the bundled read-only data dir."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = os.path.join(base, "Cartographer")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(d, "titles_sha1_user.json")


def _load_user_db() -> dict:
    try:
        with open(_user_db_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# merge any user-learned hashes over the bundled ones at import
_SHA1.update(_load_user_db())


def remember_dump(rom: bytes, title: str) -> None:
    """Record a dumped ROM's SHA-1 -> title so it resolves exactly next time."""
    digest = hashlib.sha1(rom).hexdigest().lower()
    _SHA1[digest] = {"title": title}
    user = _load_user_db()
    user[digest] = {"title": title}
    try:
        with open(_user_db_path(), "w", encoding="utf-8") as f:
            json.dump(user, f, indent=1, ensure_ascii=False, sort_keys=True)
    except OSError:
        pass


def resolve_gb(title: str, rom: bytes | None = None) -> TitleInfo:
    """Resolve a GB/GBC title. SHA-1 first if the full ROM is available."""
    if rom:
        digest = hashlib.sha1(rom).hexdigest().lower()
        hit = _SHA1.get(digest)
        if hit:
            t = hit.get("title", title) if isinstance(hit, dict) else hit
            return TitleInfo(t, "sha1", "", title)
    return TitleInfo(title or "(unknown)", "header", "", title)
