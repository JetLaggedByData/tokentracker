"""Tests for the native desktop dashboard (ui/dashboard.py).

These cover the data-shaping logic that does not require a display: status
colours, per-state source lines, and the brand map. The tkinter rendering
itself needs a GUI session and is exercised on the target machine.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from tokentracker.ui import dashboard as D


def test_status_colour_thresholds():
    assert D._status_color(None) == D.MUTED      # unknown -> muted
    assert D._status_color(0) == D.GREEN
    assert D._status_color(59) == D.GREEN
    assert D._status_color(60) == D.AMBER
    assert D._status_color(79) == D.AMBER
    assert D._status_color(80) == D.RED
    assert D._status_color(100) == D.RED


def test_source_line_live():
    assert "Live reading" in D._source_line(
        {"key": "claude", "known": True})


def test_source_line_estimate_with_counts():
    s = D._source_line({"key": "chatgpt", "known": True,
                        "estimated": True, "used": 80, "limit": 160})
    assert "Estimated · 80/160" in s


def test_source_line_chatgpt_unknown_mentions_no_official_percent():
    s = D._source_line({"key": "chatgpt", "known": False}).lower()
    assert "no usage %" in s


def test_source_line_other_unknown():
    s = D._source_line({"key": "gemini", "known": False})
    assert "No data yet" in s


def test_brand_map_has_three_providers():
    assert set(D.BRAND) == {"claude", "gemini", "chatgpt"}


def test_show_dashboard_is_safe_without_display():
    # On a headless box tkinter import/Tk() fails; show_dashboard must not raise.
    D.show_dashboard([{"provider": "Claude", "key": "claude",
                       "percent": 10.0, "known": True}], "today")
