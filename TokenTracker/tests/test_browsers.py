"""Tests for core/browsers.py - detection must be safe and well-shaped on any
platform (it returns [] off Windows rather than raising)."""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def test_detect_returns_list_and_never_raises():
    from tokentracker.core import browsers
    out = browsers.detect_installed_browsers()
    assert isinstance(out, list)
    for b in out:
        assert set(b) >= {"name", "path", "store_url"}


def test_every_browser_has_a_store_url_slot():
    from tokentracker.core import browsers
    names = [name for name, _exe, _paths in browsers._BROWSERS]
    for n in names:
        assert n in browsers.STORE_URLS  # a slot exists to fill in once published


def test_exe_on_disk_finds_a_planted_file(tmp_path, monkeypatch):
    from tokentracker.core import browsers
    # Plant a fake "chrome.exe" under a base dir and confirm _exe_on_disk finds it.
    base = tmp_path
    rel = os.path.join("Google", "Chrome", "Application", "chrome.exe")
    target = base / "Google" / "Chrome" / "Application" / "chrome.exe"
    target.parent.mkdir(parents=True)
    target.write_text("")
    monkeypatch.setattr(browsers, "_base_dirs", lambda: [str(base)])
    found = browsers._exe_on_disk([rel])
    assert found == str(target)


def test_exe_on_disk_returns_none_when_absent(tmp_path, monkeypatch):
    from tokentracker.core import browsers
    monkeypatch.setattr(browsers, "_base_dirs", lambda: [str(tmp_path)])
    assert browsers._exe_on_disk([r"Nope\nope.exe"]) is None
