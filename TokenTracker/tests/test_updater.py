"""
Tests for core/updater.py - the optional GitHub update check, which must skip
cleanly when no release URL is configured (the shipped default).
"""

import importlib
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def test_updater_skips_when_url_unconfigured():
    import tokentracker.core.updater as updater
    importlib.reload(updater)
    # The shipped default is None - the check should no-op without polling.
    assert updater.GITHUB_API_URL is None
    updater.check_for_updates()
    time.sleep(0.1)
    assert updater.update_available() is False
    assert updater.get_latest_version() is None


def test_updater_version_parsing():
    import tokentracker.core.updater as updater
    assert updater._parse_version("1.2.3") == (1, 2, 3)
    assert updater._parse_version("v0.1.0") == (0, 1, 0)
    assert updater._parse_version("2.0") > updater._parse_version("1.9.9")
