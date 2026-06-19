"""
Tests for core/server.py — Counter logic, token handling, and HTTP endpoints.

Run from the TokenTracker/ directory:
    uv run pytest tests/ -q
or:
    python -m pytest tests/ -q

These tests use a temporary HOME so they never touch the real
~/.tokentracker directory. pystray is not needed here (server.py has no UI
dependency), so no mocking is required for this module.
"""

import http.client
import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

# Make src/ importable regardless of how pytest is invoked.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


@pytest.fixture()
def server_mod(tmp_path, monkeypatch):
    """Import a fresh copy of the server module with HOME pointed at tmp_path.

    The module computes IPC_TOKEN and file paths at import time, so we set
    HOME first and reload to get a clean, isolated instance per test.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows home
    import tokentracker.core.server as server
    importlib.reload(server)
    return server


# ── Token handling ────────────────────────────────────────────────────────────

def test_token_is_64_hex(server_mod):
    assert server_mod._TOKEN_RE.match(server_mod.IPC_TOKEN)


def test_malformed_token_file_is_regenerated(server_mod):
    server_mod._TOKEN_FILE.write_text("too-short")
    tok = server_mod._get_or_create_token()
    assert server_mod._TOKEN_RE.match(tok)
    assert tok != "too-short"


def test_valid_token_file_is_reused(server_mod):
    existing = "a" * 64
    server_mod._TOKEN_FILE.write_text(existing)
    assert server_mod._get_or_create_token() == existing


# ── Counter: counts + daily reset ───────────────────────────────────────────────

def test_increment_and_get(server_mod):
    c = server_mod.Counter()
    c.increment("claude")
    c.increment("claude")
    assert c.get()["claude"] == 2


def test_unknown_provider_ignored(server_mod):
    c = server_mod.Counter()
    before = c.get()
    c.increment("not-a-provider")
    assert c.get() == before


def test_reset_zeroes_counts(server_mod):
    c = server_mod.Counter()
    c.increment("gemini")
    c.reset()
    assert all(v == 0 for v in c.get().values())


# ── ChatGPT limit-hit expiry (the TTL fix) ─────────────────────────────────────

def test_limit_hit_set_and_get(server_mod):
    c = server_mod.Counter()
    c.set_limit_hit("chatgpt", True)
    assert c.get_limit_hit("chatgpt") is True


def test_limit_hit_expires_after_ttl(server_mod):
    c = server_mod.Counter()
    c.set_limit_hit("chatgpt", True)
    # Backdate the detection beyond the rolling window.
    c._limit_hit_at["chatgpt"] = time.time() - server_mod._CHATGPT_HIT_TTL - 1
    assert c.get_limit_hit("chatgpt") is False


def test_limit_hit_false_clears(server_mod):
    c = server_mod.Counter()
    c.set_limit_hit("chatgpt", True)
    c.set_limit_hit("chatgpt", False)
    assert c.get_limit_hit("chatgpt") is False


def test_expired_hit_reported_as_unknown_in_usage_data(server_mod):
    c = server_mod.Counter()
    c.set_limit_hit("chatgpt", True)
    c._limit_hit_at["chatgpt"] = time.time() - server_mod._CHATGPT_HIT_TTL - 1
    cg = [d for d in c.usage_data() if d["key"] == "chatgpt"][0]
    assert cg["known"] is False  # shows "—", not 100%


# ── usage_data: 0% is real, not missing ─────────────────────────────────────────

def test_gemini_zero_percent_is_known(server_mod):
    c = server_mod.Counter()
    c.set_gemini_real_usage({"currentPercent": 0})
    gd = [d for d in c.usage_data() if d["key"] == "gemini"][0]
    assert gd["known"] is True
    assert gd["percent"] == 0.0


def test_claude_single_browser_not_multi_source(server_mod):
    c = server_mod.Counter()
    c.set_claude_real_usage({"browserId": "brave", "fiveHourPercent": 8})
    c.set_claude_real_usage({"browserId": "brave", "fiveHourPercent": 9})  # same browser
    cd = [d for d in c.usage_data() if d["key"] == "claude"][0]
    assert cd["multi_source"] is False


def test_claude_two_browsers_flagged_multi_source(server_mod):
    c = server_mod.Counter()
    c.set_claude_real_usage({"browserId": "brave", "fiveHourPercent": 8})
    c.set_claude_real_usage({"browserId": "edge",  "fiveHourPercent": 60})
    cd = [d for d in c.usage_data() if d["key"] == "claude"][0]
    # newest reading wins, but the conflict is surfaced
    assert cd["percent"] == 60.0
    assert cd["multi_source"] is True


def test_gemini_two_browsers_flagged_multi_source(server_mod):
    c = server_mod.Counter()
    c.set_gemini_real_usage({"browserId": "brave", "currentPercent": 0})
    c.set_gemini_real_usage({"browserId": "edge",  "currentPercent": 40})
    gd = [d for d in c.usage_data() if d["key"] == "gemini"][0]
    assert gd["multi_source"] is True


def test_source_browsers_listed_with_names(server_mod):
    c = server_mod.Counter()
    c.set_claude_real_usage({"browserId": "b1", "browserName": "Brave", "fiveHourPercent": 8})
    c.set_gemini_real_usage({"browserId": "b2", "browserName": "Edge", "currentPercent": 40})
    src = c.list_source_browsers()
    names = sorted(b["name"] for b in src)
    assert names == ["Brave", "Edge"]


def test_register_browser_appears_without_readings(server_mod):
    c = server_mod.Counter()
    c.register_browser("e", "Edge")          # no usage data yet
    c.register_browser("f", "Firefox")
    names = sorted(b["name"] for b in c.list_source_browsers())
    assert names == ["Edge", "Firefox"]      # both selectable immediately


def test_active_browser_locks_the_reading(server_mod):
    c = server_mod.Counter()
    c.set_claude_real_usage({"browserId": "b1", "browserName": "Brave", "fiveHourPercent": 8})
    c.set_claude_real_usage({"browserId": "b2", "browserName": "Edge", "fiveHourPercent": 60})
    cd = lambda: [d for d in c.usage_data() if d["key"] == "claude"][0]
    assert cd()["percent"] == 60.0          # default: newest
    c.set_active_browser("b1")
    assert cd()["percent"] == 8.0           # locked to Brave
    c.set_active_browser(None)
    assert cd()["percent"] == 60.0          # back to newest


def test_active_browser_pin_is_strict_no_fallback(server_mod):
    # A MANUAL pin must be absolute: when the pinned browser has no reading for
    # a provider, that provider shows "no data" rather than silently borrowing
    # another browser's number. (The user chose this browser explicitly.)
    c = server_mod.Counter()
    c.set_claude_real_usage({"browserId": "b1", "browserName": "Brave", "fiveHourPercent": 8})
    c.set_active_browser("ghost")           # a browser with no readings
    cd = [d for d in c.usage_data() if d["key"] == "claude"][0]
    assert cd["known"] is False             # strict: no fallback to Brave's 8
    # ...and clearing the pin restores the visible reading, no crash.
    c.set_active_browser(None)
    cd = [d for d in c.usage_data() if d["key"] == "claude"][0]
    assert cd["percent"] == 8.0


def test_claude_real_reading_used(server_mod):
    c = server_mod.Counter()
    c.set_claude_real_usage({"fiveHourPercent": 42, "resetLabel": "in 2h"})
    cd = [d for d in c.usage_data() if d["key"] == "claude"][0]
    assert cd["known"] is True
    assert cd["percent"] == 42.0
    assert cd["reset_label"] == "in 2h"


def test_percent_capped_at_100(server_mod):
    c = server_mod.Counter()
    c.set_claude_real_usage({"fiveHourPercent": 150})
    cd = [d for d in c.usage_data() if d["key"] == "claude"][0]
    assert cd["percent"] == 100.0


# ── Rate-limiter config ─────────────────────────────────────────────────────────

def test_refresh_flag_set_and_consumed_once(server_mod):
    c = server_mod.Counter()
    assert c.consume_refresh() is False          # nothing pending initially
    c.request_refresh()
    assert c.consume_refresh() is True           # tray asked -> seen once
    assert c.consume_refresh() is False          # then cleared


def test_rate_exempt_paths(server_mod):
    assert "/claude_usage" in server_mod._RATE_EXEMPT_PATHS
    assert "/gemini_usage" in server_mod._RATE_EXEMPT_PATHS
    assert "/count" not in server_mod._RATE_EXEMPT_PATHS


def test_rate_limiter_ceiling_above_legitimate_burst(server_mod):
    # A real burst is a handful of POSTs; the ceiling must be well above that.
    assert server_mod._RateLimiter()._max >= 100


# ── HTTP endpoints (live server on a real socket) ───────────────────────────────

@pytest.fixture()
def live_server(server_mod):
    counter = server_mod.Counter()
    srv = server_mod.BrowserServer(counter)
    assert srv.start(), "server failed to bind — port 7734 in use?"
    time.sleep(0.2)
    yield server_mod, counter
    srv.stop()


def _request(path, method="GET", body=None, headers=None):
    base = "http://127.0.0.1:7734"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=2)
        return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_pair_returns_token_unauthenticated(live_server):
    server_mod, _ = live_server
    status, body, _ = _request("/pair")
    assert status == 200
    assert json.loads(body)["token"] == server_mod.IPC_TOKEN


def test_protected_endpoint_rejects_missing_token(live_server):
    status, _, _ = _request("/counts")
    assert status == 403


def test_protected_endpoint_rejects_wrong_token(live_server):
    status, _, _ = _request("/counts", headers={"X-TokenTracker-Token": "wrong"})
    assert status == 403


def test_protected_endpoint_accepts_correct_token(live_server):
    server_mod, _ = live_server
    status, _, _ = _request("/counts",
                            headers={"X-TokenTracker-Token": server_mod.IPC_TOKEN})
    assert status == 200


def test_cors_reflects_extension_origin(live_server):
    server_mod, _ = live_server
    _, _, headers = _request(
        "/counts",
        headers={"X-TokenTracker-Token": server_mod.IPC_TOKEN,
                 "Origin": "chrome-extension://abcdef"})
    assert headers.get("Access-Control-Allow-Origin") == "chrome-extension://abcdef"


def test_cors_denies_web_origin(live_server):
    server_mod, _ = live_server
    _, _, headers = _request(
        "/counts",
        headers={"X-TokenTracker-Token": server_mod.IPC_TOKEN,
                 "Origin": "https://evil.example.com"})
    assert "Access-Control-Allow-Origin" not in headers


def test_oversized_body_rejected(live_server):
    server_mod, _ = live_server
    conn = http.client.HTTPConnection("127.0.0.1", 7734, timeout=2)
    conn.request("POST", "/count", body=b"",
                 headers={"X-TokenTracker-Token": server_mod.IPC_TOKEN,
                          "Content-Length": str(70 * 1024)})
    assert conn.getresponse().status == 413


def test_count_endpoint_increments(live_server):
    server_mod, counter = live_server
    status, body, _ = _request(
        "/count", method="POST", body={"provider": "claude"},
        headers={"X-TokenTracker-Token": server_mod.IPC_TOKEN,
                 "Content-Type": "application/json"})
    assert status == 200
    assert json.loads(body)["counts"]["claude"] == 1


# ── Security: Host-header validation (DNS-rebinding defence) ────────────────────

def test_pair_rejected_with_foreign_host(live_server):
    """A DNS-rebinding website reaches us with its own Host header — reject it."""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", 7734, timeout=2)
    conn.request("GET", "/pair", headers={"Host": "evil.example.com:7734"})
    assert conn.getresponse().status == 421


def test_pair_allowed_with_loopback_host(live_server):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", 7734, timeout=2)
    conn.request("GET", "/pair", headers={"Host": "127.0.0.1:7734"})
    assert conn.getresponse().status == 200


def test_post_rejected_with_foreign_host(live_server):
    server_mod, _ = live_server
    import http.client, json as _json
    conn = http.client.HTTPConnection("127.0.0.1", 7734, timeout=2)
    body = _json.dumps({"provider": "claude"})
    conn.request("POST", "/count", body=body, headers={
        "Host": "attacker.test",
        "X-TokenTracker-Token": server_mod.IPC_TOKEN,
        "Content-Type": "application/json",
    })
    assert conn.getresponse().status == 421


# ── Security: usage payload sanitisation ────────────────────────────────────────

def test_usage_percent_clamped(server_mod):
    c = server_mod.Counter()
    c.set_claude_real_usage({"fiveHourPercent": 9999})
    cd = [d for d in c.usage_data() if d["key"] == "claude"][0]
    assert cd["percent"] == 100.0


def test_usage_rejects_non_numeric_percent(server_mod):
    c = server_mod.Counter()
    c.set_gemini_real_usage({"currentPercent": "DROP TABLE"})
    gd = [d for d in c.usage_data() if d["key"] == "gemini"][0]
    # Garbage percent dropped -> gemini falls back to unknown, not crash
    assert gd["known"] is False


def test_usage_label_length_capped(server_mod):
    c = server_mod.Counter()
    c.set_claude_real_usage({"fiveHourPercent": 10, "resetLabel": "x" * 5000})
    stored = c.get_claude_real_usage()
    assert len(stored["resetLabel"]) <= 80


# ── ChatGPT estimated usage (no official %, computed from message count) ────────

def _ms(now_offset_s=0):
    import time as _t
    return int((_t.time() + now_offset_s) * 1000)


def test_chatgpt_estimate_from_timestamps(server_mod):
    c = server_mod.Counter()
    c.set_chatgpt_estimate({"browserId": "brave", "timestamps": [_ms() for _ in range(80)], "limit": 160})
    cg = [d for d in c.usage_data() if d["key"] == "chatgpt"][0]
    assert cg["known"] is True and cg["estimated"] is True
    assert cg["used"] == 80 and cg["percent"] == 50.0


def test_chatgpt_merges_across_browsers(server_mod):
    c = server_mod.Counter()
    c.set_chatgpt_estimate({"browserId": "brave", "timestamps": [_ms() for _ in range(20)], "limit": 160})
    c.set_chatgpt_estimate({"browserId": "edge",  "timestamps": [_ms() for _ in range(15)], "limit": 160})
    cg = [d for d in c.usage_data() if d["key"] == "chatgpt"][0]
    # Brave 20 + Edge 15 must combine to 35, not overwrite.
    assert cg["used"] == 35
    assert cg["percent"] == round(35 / 160 * 100, 1)


def test_chatgpt_same_browser_replaces_not_doubles(server_mod):
    c = server_mod.Counter()
    c.set_chatgpt_estimate({"browserId": "brave", "timestamps": [_ms() for _ in range(10)], "limit": 160})
    c.set_chatgpt_estimate({"browserId": "brave", "timestamps": [_ms() for _ in range(12)], "limit": 160})
    cg = [d for d in c.usage_data() if d["key"] == "chatgpt"][0]
    assert cg["used"] == 12   # same browser's latest snapshot replaces its own


def test_chatgpt_expired_timestamps_dropped(server_mod):
    c = server_mod.Counter()
    old = _ms(-(server_mod._CHATGPT_HIT_TTL + 60))   # outside the window
    c.set_chatgpt_estimate({"browserId": "brave", "timestamps": [old] * 50, "limit": 160})
    cg = [d for d in c.usage_data() if d["key"] == "chatgpt"][0]
    assert cg["known"] is False   # all expired -> unknown


def test_chatgpt_percent_capped_at_100(server_mod):
    c = server_mod.Counter()
    c.set_chatgpt_estimate({"browserId": "brave", "timestamps": [_ms() for _ in range(400)], "limit": 160})
    cg = [d for d in c.usage_data() if d["key"] == "chatgpt"][0]
    assert cg["percent"] == 100.0


def test_chatgpt_real_hit_overrides_estimate(server_mod):
    c = server_mod.Counter()
    c.set_chatgpt_estimate({"browserId": "brave", "timestamps": [_ms() for _ in range(48)], "limit": 160})
    c.set_limit_hit("chatgpt", True)
    cg = [d for d in c.usage_data() if d["key"] == "chatgpt"][0]
    assert cg["percent"] == 100.0 and cg["estimated"] is False


def test_chatgpt_unknown_when_no_data(server_mod):
    c = server_mod.Counter()
    cg = [d for d in c.usage_data() if d["key"] == "chatgpt"][0]
    assert cg["known"] is False


def test_chatgpt_reports_browser_count(server_mod):
    c = server_mod.Counter()
