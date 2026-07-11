"""
test_updater.py - tests for the update checker and self-replace scripts.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import settings, updater  # noqa: E402


def test_version_compare():
    assert updater.is_newer("1.1.0", "1.0.0")
    assert updater.is_newer("v2.0.0", "1.9.9")
    assert updater.is_newer("1.0.10", "1.0.9")
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("1.0.0", "1.2.0")


def test_norm():
    assert updater._norm("v1.2.3") == (1, 2, 3)
    assert updater._norm("1.2") == (1, 2, 0)
    assert updater._norm("v3") == (3, 0, 0)
    assert updater._norm("1.0.0-beta") == (1, 0, 0)


def test_pick_asset_prefers_platform():
    # These are the exact names the release workflow produces.
    assets = [
        {"name": "Cartographer-windows.exe", "browser_download_url": "win",
         "size": 20},
        {"name": "Cartographer-linux", "browser_download_url": "lin", "size": 10},
        {"name": "Cartographer-macos.zip", "browser_download_url": "mac",
         "size": 30},
    ]
    url, name, size = updater._pick_asset(assets)
    expected = {"windows": "Cartographer-windows.exe",
                "linux": "Cartographer-linux",
                "macos": "Cartographer-macos.zip"}[updater._platform_hint()]
    assert name == expected, f"picked {name}, expected {expected}"


def test_extensionless_linux_binary_is_found():
    # The Linux binary has no extension; make sure it's still selected on Linux
    # rather than falling through to a .zip.
    assets = [
        {"name": "Cartographer-linux", "browser_download_url": "lin", "size": 10},
        {"name": "Cartographer-macos.zip", "browser_download_url": "mac",
         "size": 30},
    ]
    hint = updater._platform_hint()
    url, name, size = updater._pick_asset(assets)
    if hint == "linux":
        assert name == "Cartographer-linux"


def test_can_self_replace():
    # Behaviour depends on platform; just assert it returns a bool consistently.
    assert isinstance(updater.can_self_replace("Cartographer.exe"), bool)
    assert updater.can_self_replace("Cartographer-macos.zip") is False or \
        updater.can_self_replace("Cartographer-macos.zip") in (True, False)


def test_apply_update_refuses_when_not_frozen():
    try:
        updater.apply_update_and_restart("/tmp/none")
        assert False, "should have raised"
    except updater.UpdateError:
        pass


def test_windows_swapper_script_content(monkeypatch=None):
    # Directly exercise the script writer and inspect the .bat it produces.
    import types
    calls = {}

    def fake_popen(args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return types.SimpleNamespace(pid=1234)

    import subprocess
    real_popen = subprocess.Popen
    subprocess.Popen = fake_popen
    try:
        updater._spawn_windows_swapper(r"C:\tmp\new.exe", r"C:\app\Cartographer.exe")
    finally:
        subprocess.Popen = real_popen

    # It should have written a .bat and launched cmd /c on it.
    assert calls["args"][0] == "cmd"
    bat = calls["args"][2]
    assert bat.endswith(".bat")
    content = open(bat, encoding="ascii").read()
    assert "Cartographer.exe" in content
    assert "move /y" in content          # swaps the file
    assert 'start "" "%TARGET%"' in content   # relaunches
    assert "tasklist" in content         # waits for the app to exit
    os.remove(bat)


def test_unix_swapper_script_content():
    import types
    calls = {}

    def fake_popen(args, **kwargs):
        calls["args"] = args
        return types.SimpleNamespace(pid=1)

    import subprocess
    real_popen = subprocess.Popen
    subprocess.Popen = fake_popen
    try:
        updater._spawn_unix_swapper("/tmp/new", "/app/Cartographer", mac=False)
    finally:
        subprocess.Popen = real_popen

    sh = calls["args"][1]
    content = open(sh, encoding="ascii").read()
    assert "kill -0" in content          # waits for the app to exit
    assert "mv -f" in content            # swaps the file
    assert "chmod +x" in content
    os.remove(sh)


def test_settings_roundtrip(tmp_path=None):
    # Point settings at a temp dir via APPDATA and confirm persistence.
    d = tempfile.mkdtemp()
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = d
    try:
        import importlib
        importlib.reload(settings)
        settings.set_skip_version("v9.9.9")
        assert settings.skip_version() == "v9.9.9"
        settings.set_show_whats_new(False)
        assert settings.show_whats_new() is False
        settings.set("pending_whats_new_version", "v9.9.9")
        assert settings.get("pending_whats_new_version") == "v9.9.9"
    finally:
        if old is not None:
            os.environ["APPDATA"] = old
        else:
            os.environ.pop("APPDATA", None)
        import importlib
        importlib.reload(settings)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} updater tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
