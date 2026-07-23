"""
settings.py - tiny persistent settings store.

Holds a handful of user preferences (whether to show the What's New window, which
version they chose to skip) in a JSON file under the user's config directory. The
same directory the title database uses.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import json
import os


def _config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = os.path.join(base, "Cartographer")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.expanduser("~")
    return d


_PATH = os.path.join(_config_dir(), "settings.json")

_DEFAULTS = {
    "show_whats_new": True,     # pop the What's New window after an update
    "skip_version": "",         # a version tag the user chose to skip
    "last_seen_version": "",    # last version we showed What's New for
    "output_folder": "",        # default folder for dumps ("" = ask each time)
    "auto_verify": True,        # verify ROM dumps automatically
    "write_dump_report": True,  # write a verification report next to each dump
    "check_updates_on_start": True,
    "library_folder": "",       # folder the library view scans
    "save_override": "",        # save type forced by hand ("" = use detection)
}


def _load() -> dict:
    data = dict(_DEFAULTS)
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data.update(json.load(f))
    except (OSError, ValueError):
        pass
    return data


def _save(data: dict) -> None:
    # Persist only values that differ from the defaults. Writing every key
    # would pin today's defaults into the file, and a later change to a
    # default would then never reach anyone who has already run the app.
    _sentinel = object()
    delta = {k: v for k, v in data.items()
             if _DEFAULTS.get(k, _sentinel) != v}
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(delta, f, indent=1)
    except OSError:
        pass


def get(key: str):
    return _load().get(key, _DEFAULTS.get(key))


def set(key: str, value) -> None:  # noqa: A001 - deliberate simple API
    data = _load()
    data[key] = value
    _save(data)


def skip_version() -> str:
    return _load().get("skip_version", "")


def set_skip_version(tag: str) -> None:
    set("skip_version", tag or "")


def show_whats_new() -> bool:
    return bool(_load().get("show_whats_new", True))


def set_show_whats_new(on: bool) -> None:
    set("show_whats_new", bool(on))
