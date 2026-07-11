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
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
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
