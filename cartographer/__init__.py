"""
Cartographer - a cross-platform reader, writer and flasher for Game Boy,
Game Boy Color and Game Boy Advance cartridges over a GBxCart RW compatible
device.

Dump ROMs and saves, restore saves, verify dumps against known-good hashes,
resolve full game titles, identify flash chips, and patch GBA saves for
batteryless carts.

Written by LJ "HawaiizFynest" Eblacas
"""

import os as _os
import re as _re
import sys as _sys

__app_name__ = "Cartographer"

# The version has a single source of truth: the top "## vX.Y.Z" heading in
# CHANGELOG.md. Editing the changelog for a release is enough - the app version
# follows it automatically, so the source can't drift behind the tag and cause a
# phantom "update available".
#
# The hardcoded string below is only a last-resort fallback for the rare case
# where the changelog can't be read (it should always match the changelog's top
# entry anyway). CI still overwrites this string from the git tag at build time,
# which stays consistent with reading the changelog.
_FALLBACK_VERSION = "1.0.6"


def _changelog_path() -> str:
    base = getattr(_sys, "_MEIPASS", None)
    if base:
        return _os.path.join(base, "CHANGELOG.md")
    return _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "CHANGELOG.md")


def _version_from_changelog() -> str:
    try:
        with open(_changelog_path(), encoding="utf-8") as f:
            for line in f:
                m = _re.match(r"^##\s*v?(\d+\.\d+\.\d+)", line.strip())
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


__version__ = _version_from_changelog() or _FALLBACK_VERSION
