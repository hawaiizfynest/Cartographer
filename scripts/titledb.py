"""
titledb.py - manage Cartographer's local SHA-1 title database.

FlashGBX and similar tools resolve the exact game release by matching the full
ROM's hash against a No-Intro-derived table. Cartographer ships a small game-code
table for instant resolution, and this tool lets you grow an exact-match SHA-1
table from ROMs you have dumped yourself - so once you have backed up a cart, its
precise title resolves on every future connect.

Usage:
    # add a dumped ROM under a title you provide (or its header short title)
    python scripts/titledb.py add mygame.gba --title "Game Name (USA)"

    # add every .gba/.gb/.gbc in a folder, using each file's own name as title
    python scripts/titledb.py add-folder ./dumps

    # show how many entries are stored
    python scripts/titledb.py count

The database is cartographer/data/titles_sha1.json (created on first add).

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "cartographer", "data")
_DB_PATH = os.path.join(_DATA_DIR, "titles_sha1.json")


def _load() -> dict:
    try:
        with open(_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(db: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=1, ensure_ascii=False, sort_keys=True)


def _gba_short_title(rom: bytes) -> str:
    if len(rom) >= 0xB0:
        return rom[0xA0:0xAC].decode("ascii", "replace").strip("\x00 ")
    return ""


def _add_file(db: dict, path: str, title: str | None) -> str:
    with open(path, "rb") as f:
        rom = f.read()
    digest = hashlib.sha1(rom).hexdigest().lower()
    if not title:
        title = _gba_short_title(rom) or os.path.splitext(os.path.basename(path))[0]
    db[digest] = {"title": title}
    return f"{digest[:12]}\u2026 -> {title}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Manage the local SHA-1 title DB")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="add a single ROM")
    p_add.add_argument("rom")
    p_add.add_argument("--title")

    p_folder = sub.add_parser("add-folder", help="add all ROMs in a folder")
    p_folder.add_argument("folder")

    sub.add_parser("count", help="show entry count")

    args = ap.parse_args(argv)
    db = _load()

    if args.cmd == "add":
        if not os.path.isfile(args.rom):
            print(f"Not a file: {args.rom}")
            return 1
        print("Added:", _add_file(db, args.rom, args.title))
        _save(db)
    elif args.cmd == "add-folder":
        exts = (".gba", ".gb", ".gbc")
        n = 0
        for name in sorted(os.listdir(args.folder)):
            if name.lower().endswith(exts):
                print("Added:", _add_file(db, os.path.join(args.folder, name), None))
                n += 1
        _save(db)
        print(f"{n} ROM(s) added.")
    elif args.cmd == "count":
        print(f"{len(db)} SHA-1 entries in {_DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
