"""
updater.py - check GitHub Releases for a newer version and download it.

Checks the latest release published on the project's GitHub repo, compares it to
the running version, and (if the user agrees) downloads the platform asset with
integrity checks: it verifies the HTTP Content-Length matches the bytes received
and, on Windows, that the downloaded file starts with the "MZ" executable magic.
Downloads are retried a few times before giving up.

The updater only ever downloads to a file and hands the path back to the caller -
it does not silently replace the running program.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass

# Point this at the project's GitHub repository.
GITHUB_OWNER = "HawaiizFynest"
GITHUB_REPO = "Cartographer"
_API_LATEST = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

_USER_AGENT = f"{GITHUB_REPO}-updater"
_TIMEOUT = 15
_RETRIES = 3


@dataclass
class ReleaseInfo:
    version: str
    tag: str
    name: str
    notes: str
    html_url: str
    asset_url: str        # best-matching downloadable asset for this platform
    asset_name: str
    asset_size: int


class UpdateError(Exception):
    pass


def _norm(v: str) -> tuple:
    """Turn 'v1.2.3' into (1, 2, 3) for comparison; non-numeric parts ignored."""
    v = v.strip().lstrip("vV")
    parts = []
    for chunk in v.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(remote: str, local: str) -> bool:
    return _norm(remote) > _norm(local)


def _platform_hint() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _pick_asset(assets: list) -> tuple:
    """Choose the asset that best matches this platform; fall back to the first."""
    hint = _platform_hint()
    ext = {"windows": (".exe", ".zip"), "macos": (".dmg", ".zip"),
           "linux": (".appimage", ".tar.gz", ".zip")}[hint]
    for a in assets:
        name = a.get("name", "").lower()
        if hint in name and name.endswith(ext):
            return a.get("browser_download_url", ""), a.get("name", ""), \
                a.get("size", 0)
    for a in assets:
        name = a.get("name", "").lower()
        if name.endswith(ext):
            return a.get("browser_download_url", ""), a.get("name", ""), \
                a.get("size", 0)
    if assets:
        a = assets[0]
        return a.get("browser_download_url", ""), a.get("name", ""), \
            a.get("size", 0)
    return "", "", 0


def fetch_latest() -> ReleaseInfo:
    """Query GitHub for the latest release. Raises UpdateError on failure."""
    req = urllib.request.Request(_API_LATEST, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    last_err = None
    for attempt in range(_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    else:
        raise UpdateError(f"Could not reach GitHub: {last_err}")

    tag = data.get("tag_name", "")
    assets = data.get("assets", []) or []
    url, name, size = _pick_asset(assets)
    return ReleaseInfo(
        version=tag.lstrip("vV"), tag=tag, name=data.get("name", tag),
        notes=data.get("body", "") or "", html_url=data.get("html_url", ""),
        asset_url=url, asset_name=name, asset_size=size)


def download_asset(rel: ReleaseInfo, dest_dir: str,
                   progress=None) -> str:
    """Download the release asset to dest_dir with integrity checks.

    Verifies Content-Length and (on Windows .exe) the MZ header. Retries on
    failure. Returns the downloaded file path. Raises UpdateError otherwise.
    """
    if not rel.asset_url:
        raise UpdateError("This release has no downloadable file for your "
                          "platform. Opening the releases page instead.")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, rel.asset_name or "update.bin")

    last_err = None
    for attempt in range(_RETRIES):
        try:
            req = urllib.request.Request(rel.asset_url,
                                         headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length", "0") or 0)
                got = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if progress and total:
                            progress(got, total)
            # integrity: byte count must match the advertised size
            if total and got != total:
                raise UpdateError(
                    f"Download incomplete ({got} of {total} bytes).")
            # integrity: Windows executables must start with 'MZ'
            if dest.lower().endswith(".exe"):
                with open(dest, "rb") as f:
                    if f.read(2) != b"MZ":
                        raise UpdateError("Downloaded file is not a valid "
                                          "Windows executable (bad header).")
            return dest
        except UpdateError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise UpdateError(f"Download failed after {_RETRIES} attempts: {last_err}")
