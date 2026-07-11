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
    """Choose the asset that best matches this platform; fall back to the first.

    Priority: an asset whose name contains the platform hint (e.g. 'windows',
    'linux', 'macos') wins outright - that is how the release workflow names them.
    Only if none match by name do we fall back to matching by file extension, and
    finally to the first asset.
    """
    hint = _platform_hint()
    # 1. Best: the name explicitly carries the platform.
    for a in assets:
        if hint in a.get("name", "").lower():
            return (a.get("browser_download_url", ""), a.get("name", ""),
                    a.get("size", 0))
    # 2. Otherwise match by a plausible extension for this platform.
    ext = {"windows": (".exe",), "macos": (".dmg", ".zip", ".app"),
           "linux": (".appimage", ".tar.gz", ".zip")}[hint]
    for a in assets:
        if a.get("name", "").lower().endswith(ext):
            return (a.get("browser_download_url", ""), a.get("name", ""),
                    a.get("size", 0))
    # 3. Last resort.
    if assets:
        a = assets[0]
        return (a.get("browser_download_url", ""), a.get("name", ""),
                a.get("size", 0))
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


def _bundled_changelog_path() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "CHANGELOG.md")
    # source tree: repo root is two levels up from this file
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "CHANGELOG.md")


def changelog_section(version: str) -> str:
    """Return the plain-text notes for a version from the bundled CHANGELOG.md.

    Matches a heading like '## v1.0.1' (with or without the leading 'v') and
    returns everything up to the next '## ' heading. Empty string if not found.
    """
    ver = version.strip().lstrip("vV")
    try:
        text = open(_bundled_changelog_path(), encoding="utf-8").read()
    except OSError:
        return ""
    lines = text.splitlines()
    out = []
    grabbing = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip().lstrip("vV")
            if grabbing:
                break
            grabbing = (heading == ver)
            continue
        if grabbing:
            out.append(line)
    return "\n".join(out).strip()


def best_notes(version: str, github_notes: str) -> str:
    """Prefer the hand-written changelog section; fall back to GitHub's notes."""
    section = changelog_section(version)
    if section:
        return section
    return (github_notes or "").strip()


def current_executable() -> str:
    """Path to the currently running program.

    When frozen by PyInstaller this is the .exe/.app binary; otherwise it's the
    Python interpreter running the scripts (self-replace only makes sense for a
    frozen build).
    """
    return sys.executable


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def download_asset(rel: ReleaseInfo, dest_dir: str, progress=None) -> str:
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


def apply_update_and_restart(downloaded: str) -> None:
    """Replace the running program with `downloaded`, then relaunch it.

    A running executable can't overwrite itself while it's running, so this spawns
    a small detached helper that waits for this process to exit, swaps the files,
    and starts the new version. This function returns after launching the helper;
    the caller should then quit the app immediately.

    Only meaningful for a frozen (PyInstaller) build. Raises UpdateError if the
    downloaded file isn't usable.
    """
    if not is_frozen():
        raise UpdateError(
            "Automatic replace only works in the built app. From source, pull the "
            "new code with GitHub Desktop instead.")
    target = current_executable()
    if not os.path.exists(downloaded):
        raise UpdateError("Downloaded file is missing.")

    if sys.platform.startswith("win"):
        _spawn_windows_swapper(downloaded, target)
    elif sys.platform == "darwin":
        _spawn_unix_swapper(downloaded, target, mac=True)
    else:
        _spawn_unix_swapper(downloaded, target, mac=False)


def _spawn_windows_swapper(src: str, dst: str) -> None:
    """Write a .bat that waits for the app to close, swaps the exe, relaunches."""
    import subprocess
    import tempfile

    pid = os.getpid()
    bat = os.path.join(tempfile.gettempdir(), f"cartographer_update_{pid}.bat")
    # Wait for our PID to disappear, back up the old exe, move the new one in,
    # relaunch, then delete ourselves.
    script = f"""@echo off
setlocal
set "TARGET={dst}"
set "SRC={src}"
:waitloop
tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL
if not errorlevel 1 (
    ping -n 2 127.0.0.1 >NUL
    goto waitloop
)
if exist "%TARGET%.old" del /f /q "%TARGET%.old" >NUL 2>&1
if exist "%TARGET%" move /y "%TARGET%" "%TARGET%.old" >NUL 2>&1
move /y "%SRC%" "%TARGET%" >NUL 2>&1
start "" "%TARGET%"
del /f /q "%TARGET%.old" >NUL 2>&1
del /f /q "%~f0" >NUL 2>&1
"""
    with open(bat, "w", encoding="ascii") as f:
        f.write(script)
    # Detached, no console window.
    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008
    subprocess.Popen(["cmd", "/c", bat],
                     creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                     close_fds=True)


def _spawn_unix_swapper(src: str, dst: str, mac: bool) -> None:
    """Write a shell script that waits for the app to exit, swaps, relaunches."""
    import subprocess
    import tempfile

    pid = os.getpid()
    sh = os.path.join(tempfile.gettempdir(), f"cartographer_update_{pid}.sh")
    relaunch = (f'open "{dst}"' if mac else f'"{dst}" &')
    # For a downloaded archive (.zip/.dmg) we can't move a single binary; in that
    # case just open the download location. Here we handle the direct-binary case.
    script = f"""#!/bin/sh
while kill -0 {pid} 2>/dev/null; do sleep 0.5; done
if [ -f "{dst}" ]; then mv -f "{dst}" "{dst}.old" 2>/dev/null; fi
mv -f "{src}" "{dst}" 2>/dev/null
chmod +x "{dst}" 2>/dev/null
{relaunch}
rm -f "{dst}.old" 2>/dev/null
rm -f "$0" 2>/dev/null
"""
    with open(sh, "w", encoding="ascii") as f:
        f.write(script)
    os.chmod(sh, 0o755)
    subprocess.Popen(["/bin/sh", sh], close_fds=True,
                     start_new_session=True)


def can_self_replace(asset_name: str) -> bool:
    """True if the downloaded asset is a single binary we can swap in place.

    A zipped/dmg release can't be swapped byte-for-byte, so those fall back to
    'open the folder' behaviour.
    """
    name = (asset_name or "").lower()
    if sys.platform.startswith("win"):
        return name.endswith(".exe")
    if sys.platform == "darwin":
        return name.endswith(".app")   # rare; usually zipped
    return not (name.endswith(".zip") or name.endswith(".tar.gz")
                or name.endswith(".dmg"))

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
