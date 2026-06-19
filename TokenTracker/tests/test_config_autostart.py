"""Tests for the first-run autostart config flag (Consumer Edition one-click)."""

import importlib
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


@pytest.fixture()
def config_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import tokentracker.core.config as config
    importlib.reload(config)
    return config


def test_autostart_not_prompted_initially(config_mod):
    assert config_mod.autostart_prompted() is False


def test_mark_autostart_prompted_persists(config_mod):
    config_mod.mark_autostart_prompted()
    assert config_mod.autostart_prompted() is True


def test_autostart_flag_survives_reload(config_mod, tmp_path, monkeypatch):
    config_mod.mark_autostart_prompted()
    # Reload as if the app restarted — the flag must persist on disk.
    import tokentracker.core.config as config
    importlib.reload(config)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.autostart_prompted() is True
