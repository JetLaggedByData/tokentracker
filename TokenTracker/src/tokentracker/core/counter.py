"""
core/counter.py - usage state and the merge logic (the "engine").

This is pure business logic with no knowledge of HTTP. The Counter holds every
provider's current reading, merges readings that arrive from multiple browsers,
turns ChatGPT's raw message timestamps into an estimated percentage, and tells
the tray which browsers are selectable as a source. server.py is a thin HTTP
adapter over this class; tests exercise Counter directly without a socket.

Persistence: a small JSON file (~/.tokentracker/browser_counts.json) holds the
daily counts so they survive a restart. The path is resolved per-instance from
Path.home() so tests can isolate it via a patched HOME.
"""

import json
import logging
import threading
import time as _time_mod
from datetime import date
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# ChatGPT's message limit is a ~3-hour rolling window. A detected limit-hit is
# treated as valid for this long, then expires - otherwise a single hit (or a
# false positive) would pin ChatGPT at 100% forever.
_CHATGPT_HIT_TTL = 3 * 60 * 60   # seconds

# Daily message limits per provider (approximate - not officially published).
# Copilot deliberately excluded: company/M365 accounts expose no personal usage
# signal at all, so there is nothing real to track or display.
PROVIDER_LIMITS = {
    "claude":  40,
    "gemini":  40,
    "chatgpt": 40,
}

PROVIDER_NAMES = {
    "claude":  "Claude",
    "gemini":  "Gemini",
    "chatgpt": "ChatGPT",
}

# When the user has NOT manually pinned a source browser, prefer this browser's
# reading over plain "newest wins". Most people use one browser, so this only
# matters when several report at once; preferring a named default keeps the
# shown number stable instead of flip-flopping. Case-insensitive match on the
# browser's reported name.
DEFAULT_SOURCE_BROWSER = "Chrome"


def _sanitize_usage(data: dict, pct_key: str) -> dict:
    """Validate/clamp an incoming usage payload before it is stored.
    Accepts only known-shaped values: a numeric percent in 0-100 and short
    string labels. Anything else is dropped. Prevents a token-holding caller
    from injecting oversized or malformed values into the tray UI."""
    if not isinstance(data, dict):
        return {}
    out = {}
    pct = data.get(pct_key)
    if isinstance(pct, (int, float)) and not isinstance(pct, bool):
        out[pct_key] = max(0.0, min(100.0, float(pct)))
    for k in ("resetLabel", "weeklyReset", "tier"):
        v = data.get(k)
        if isinstance(v, str):
            out[k] = v[:80]   # cap label length
    wp = data.get("weeklyPercent")
    if isinstance(wp, (int, float)) and not isinstance(wp, bool):
        out["weeklyPercent"] = max(0.0, min(100.0, float(wp)))
    return out


class Counter:
    def __init__(self):
        self._lock  = threading.Lock()
        self._today = str(date.today())
        self._counts = {k: 0 for k in PROVIDER_LIMITS}
        self._claude_real = {}
        self._gemini_real = {}
        # Per-browser store so the tray can lock onto ONE browser's readings
        # instead of last-write-wins flip-flopping between e.g. Brave + Edge.
        # browser_id -> {"claude": {...}, "gemini": {...}, "name": str, "last": epoch}
        self._browser_readings = {}
        # None = "newest wins" (legacy default); else a browser_id to lock to.
        self._active_browser = None
        # Set by the tray's "Refresh now" menu; the extension polls /health,
        # sees it, pulls fresh readings, and the flag auto-clears on read.
        self._refresh_requested = False
        # provider -> epoch seconds when the limit banner was last detected.
        self._limit_hit_at = {}
        # ChatGPT estimate is merged across browsers: each browser sends its
        # raw message timestamps tagged by a browser id; we pool them so two
        # browsers (e.g. Brave + Edge) combine instead of overwriting.
        self._chatgpt_msg_times = {}   # browser_id -> [epoch_seconds, ...]
        # Default ChatGPT message limit per rolling window. Per OpenAI's Help
        # Center, Plus/Go = 160 messages with GPT-5.5 per 3 hours (verified
        # 2026-06; OpenAI changes this over time/per plan). Overridable: the
        # extension can send a different `limit` with each push.
        self._chatgpt_limit = 160
        self._chatgpt_window = _CHATGPT_HIT_TTL  # 3h rolling window (seconds)
        # Track which browsers have recently reported each REAL-reading provider
        # (claude/gemini). If two different browsers report the same provider
        # within this window, the single shown % could be two different
        # accounts, so we flag multiSource rather than silently flip.
        self._provider_writers = {}    # provider -> {browser_id: last_epoch}
        self._MULTI_SOURCE_WINDOW = 15 * 60   # seconds
        self._SOURCE_WINDOW = 6 * 60 * 60   # how long a browser stays pickable (6h)
        self._callbacks = []
        self._cb_lock = threading.Lock()
        self._counts_file = Path.home() / ".tokentracker" / "browser_counts.json"
        self._counts_file.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def increment(self, provider: str) -> dict:
        if provider not in PROVIDER_LIMITS:
            return self._counts.copy()
        with self._lock:
            self._maybe_reset()
            self._counts[provider] = self._counts.get(provider, 0) + 1
            self._save()
        self._fire_callbacks()
        return self._counts.copy()

    def get(self) -> dict:
        with self._lock:
            self._maybe_reset()
            return self._counts.copy()

    def _record_writer(self, provider: str, data: dict) -> None:
        """Note which browser reported this provider; prune stale writers.
        Caller must hold self._lock."""
        now = _time_mod.time()
        bid = data.get("browserId") if isinstance(data, dict) else None
        bid = bid if isinstance(bid, str) and bid else "default"
        writers = self._provider_writers.setdefault(provider, {})
        writers[bid] = now
        for b, t in list(writers.items()):
            if now - t >= self._MULTI_SOURCE_WINDOW:
                del writers[b]

    def _multi_source(self, provider: str) -> bool:
        """True if >1 distinct browser reported this provider recently.
        Caller must hold self._lock."""
        return len(self._provider_writers.get(provider, {})) > 1

    def _record_browser_reading(self, provider: str, data: dict, clean: dict) -> None:
        """Store a sanitized reading under its source browser so the tray can
        later lock onto one browser. Caller must hold self._lock."""
        now = _time_mod.time()
        bid = data.get("browserId") if isinstance(data, dict) else None
        bid = bid if isinstance(bid, str) and bid else "default"
        entry = self._browser_readings.setdefault(bid, {})
        entry[provider] = dict(clean) if isinstance(clean, dict) else {}
        # Resolve a display name: incoming browserName (capped) -> existing -> "Browser".
        name = data.get("browserName") if isinstance(data, dict) else None
        if isinstance(name, str) and name:
            entry["name"] = name[:40]
        elif not isinstance(entry.get("name"), str) or not entry.get("name"):
            entry["name"] = "Browser"
        entry["last"] = now

    def set_claude_real_usage(self, data: dict) -> None:
        clean = _sanitize_usage(data, "fiveHourPercent")
        with self._lock:
            self._record_writer("claude", data)
            # Newest single-slot value is still kept (legacy "newest wins").
            self._claude_real = clean
            # Also record this browser's reading so it can be selected later.
            self._record_browser_reading("claude", data, clean)
        self._fire_callbacks()

    def get_claude_real_usage(self) -> dict:
        with self._lock:
            return dict(self._claude_real) if self._claude_real else {}

    def set_gemini_real_usage(self, data: dict) -> None:
        clean = _sanitize_usage(data, "currentPercent")
        with self._lock:
            self._record_writer("gemini", data)
            # Newest single-slot value is still kept (legacy "newest wins").
            self._gemini_real = clean
            # Also record this browser's reading so it can be selected later.
            self._record_browser_reading("gemini", data, clean)
        self._fire_callbacks()

    def get_gemini_real_usage(self) -> dict:
        with self._lock:
            return dict(self._gemini_real) if self._gemini_real else {}

    def list_source_browsers(self) -> list:
        """Every browser that has reported ANY usage (Claude, Gemini, or
        ChatGPT) within the source window, as [{"id", "name"}], sorted
        alphabetically by name. A browser that only uses ChatGPT still shows."""
        now = _time_mod.time()
        seen = {}   # bid -> name
        with self._lock:
            for bid, entry in self._browser_readings.items():
                if not isinstance(entry, dict):
                    continue
                last = entry.get("last", 0)
                if isinstance(last, (int, float)) and now - last < self._SOURCE_WINDOW:
                    name = entry.get("name")
                    seen[bid] = name if isinstance(name, str) and name else "Browser"
            # Include browsers known only from ChatGPT timestamps too.
            for bid, times in self._chatgpt_msg_times.items():
                if bid in seen:
                    continue
                if any(now - t < self._chatgpt_window for t in (times or [])):
                    nm = self._browser_readings.get(bid, {}).get("name")
                    seen[bid] = nm if isinstance(nm, str) and nm else "Browser"
        out = [{"id": b, "name": n} for b, n in seen.items()]
        out.sort(key=lambda b: b["name"].lower())
        return out

    def get_active_browser(self) -> Optional[str]:
        """Return the locked browser id, or None for "newest wins"."""
        with self._lock:
            return self._active_browser

    def get_effective_source(self) -> Optional[str]:
        """The browser id whose reading is ACTUALLY shown right now:
          - the manual pin if one is set; else
          - the default browser (Chrome) if it is open; else
          - None, meaning true "newest wins".
        The tray uses this to put the radio dot on the browser you're really
        seeing, instead of always parking it on "All - newest"."""
        with self._lock:
            if self._active_browser is not None:
                return self._active_browser
            return self._default_source_id()

    def set_active_browser(self, browser_id) -> None:
        """Lock the tray onto a browser id, or None to restore "newest wins"."""
        with self._lock:
            self._active_browser = browser_id
        self._fire_callbacks()

    def register_browser(self, browser_id, name) -> None:
        """Announce a browser (id + display name) even before it has any
        readings, so it can be picked in the tray's source list right away."""
        if not isinstance(browser_id, str) or not browser_id:
            return
        with self._lock:
            entry = self._browser_readings.setdefault(browser_id, {})
            if isinstance(name, str) and name:
                entry["name"] = name[:40]
            elif not entry.get("name"):
                entry["name"] = "Browser"
            entry["last"] = _time_mod.time()
        self._fire_callbacks()

    def request_refresh(self) -> None:
        """Tray asked for a fresh pull; the extension will see this via /health."""
        with self._lock:
            self._refresh_requested = True

    def consume_refresh(self) -> bool:
        """Return whether a refresh was requested, clearing the flag."""
        with self._lock:
            r = self._refresh_requested
            self._refresh_requested = False
            return r

    def set_limit_hit(self, provider: str, hit: bool) -> None:
        """Store whether a provider's limit-reached banner was detected.
        A hit is timestamped so it can expire once the rolling window has
        passed; hit=False clears it."""
        with self._lock:
            if hit:
                self._limit_hit_at[provider] = _time_mod.time()
            else:
                self._limit_hit_at.pop(provider, None)
        self._fire_callbacks()

    def set_chatgpt_estimate(self, data: dict) -> None:
        """Merge one browser's ESTIMATED ChatGPT activity into the pooled total.

        OpenAI exposes no official %, so each browser sends its raw outgoing
        message timestamps tagged by a per-browser id. We store the timestamps
        per browser and compute the combined estimate across all browsers in
        usage_data(), so Brave + Edge add up instead of overwriting each other.

        Accepted shapes (both validated/clamped):
          {"browserId": "...", "timestamps": [epoch_ms, ...], "limit": 160}
          {"percent": N, "used": N, "limit": N}   # legacy single-browser push
        """
        if not isinstance(data, dict):
            return
        now = _time_mod.time()
        with self._lock:
            lim = data.get("limit")
            if isinstance(lim, (int, float)) and not isinstance(lim, bool) and lim > 0:
                self._chatgpt_limit = int(lim)
            win = self._chatgpt_window

            ts = data.get("timestamps")
            if isinstance(ts, list):
                bid = data.get("browserId")
                bid = bid if isinstance(bid, str) and bid else "default"
                # Normalise to epoch seconds, keep only in-window, cap count.
                clean = []
                for t in ts[:1000]:
                    if isinstance(t, (int, float)) and not isinstance(t, bool):
                        secs = float(t) / 1000.0 if t > 1e12 else float(t)
                        if 0 < now - secs < win:
                            clean.append(secs)
                self._chatgpt_msg_times[bid] = clean
                # Register this browser (with its name) so it appears in the
                # tray's source list even if it only reports ChatGPT.
                bn = data.get("browserName")
                entry = self._browser_readings.setdefault(bid, {})
                if isinstance(bn, str) and bn:
                    entry["name"] = bn[:40]
                elif not entry.get("name"):
                    entry["name"] = "Browser"
                entry["last"] = now
            else:
                # Legacy: a pre-computed count from one browser. Synthesise
                # `used` timestamps for the default bucket so the merge still
                # works (best-effort backward compatibility).
                used = data.get("used")
                if isinstance(used, (int, float)) and not isinstance(used, bool) and used >= 0:
                    self._chatgpt_msg_times["default"] = [now] * min(int(used), 1000)
        self._fire_callbacks()

    def _chatgpt_merged(self):
        """Pooled estimate across all browsers. Caller must hold self._lock.
        Returns {percent, used, limit, estimated} or {} if no activity."""
        now = _time_mod.time()
        win = self._chatgpt_window
        used = 0
        for bid, times in list(self._chatgpt_msg_times.items()):
            kept = [t for t in times if now - t < win]
            self._chatgpt_msg_times[bid] = kept   # prune expired in place
            used += len(kept)
        if used <= 0:
            return {}
        lim = self._chatgpt_limit or 160
        pct = min(100.0, round(used / lim * 100, 1))
        browsers = sum(1 for t in self._chatgpt_msg_times.values() if t)
        return {"percent": pct, "used": used, "limit": lim,
                "browsers": browsers, "estimated": True}

    def get_limit_hit(self, provider: str) -> bool:
        with self._lock:
            return self._limit_hit_active(provider)

    def _limit_hit_active(self, provider: str) -> bool:
        """True only if a hit was detected within the rolling window.
        Caller must hold self._lock."""
        ts = self._limit_hit_at.get(provider)
        if ts is None:
            return False
        if _time_mod.time() - ts >= _CHATGPT_HIT_TTL:
            self._limit_hit_at.pop(provider, None)
            return False
        return True

    def sync_from_extension(self, counts: dict) -> None:
        with self._lock:
            self._maybe_reset()
            for k in PROVIDER_LIMITS:
                if k in counts and isinstance(counts[k], int):
                    self._counts[k] = counts[k]
            self._save()
        self._fire_callbacks()

    def reset(self) -> dict:
        with self._lock:
            self._counts = {k: 0 for k in PROVIDER_LIMITS}
            self._today  = str(date.today())
            self._save()
        self._fire_callbacks()
        return self._counts.copy()

    def on_change(self, cb: Callable) -> None:
        with self._cb_lock:
            self._callbacks.append(cb)

    def _default_source_id(self):
        """browser_id of the preferred default source (DEFAULT_SOURCE_BROWSER)
        if it is present within the source window, else None. Caller holds
        self._lock.

        Note: we return Chrome as soon as it is REGISTERED (open), not only once
        it has a reading. That way Chrome is the default the moment it's running;
        if it has not synced a provider yet, the caller shows that provider as
        "no data" (the X battery) rather than borrowing another browser's number.
        """
        now = _time_mod.time()
        want = DEFAULT_SOURCE_BROWSER.lower()
        for bid, entry in self._browser_readings.items():
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            last = entry.get("last", 0)
            if (isinstance(name, str) and name.lower() == want
                    and isinstance(last, (int, float))
                    and now - last < self._SOURCE_WINDOW):
                return bid
        return None

    def usage_data(self) -> list:
        """Return list of UsageData-compatible dicts for TrayApp."""
        with self._lock:
            self._maybe_reset()
            # By default use the newest single-slot reading. If a specific
            # browser is locked AND it has a reading for the provider, use
            # that browser's stored reading instead; otherwise fall back to
            # the newest single-slot value.
            # Which browser's reading to show:
            #   1. a manual pin (self._active_browser) wins ABSOLUTELY - we show
            #      ONLY that browser's readings, with no fallback to other
            #      browsers (if it has no reading for a provider, that provider
            #      shows "no data" rather than silently borrowing another
            #      browser's number - a manual choice must mean exactly itself);
            #   2. else the preferred default browser (Chrome) if it is reporting;
            #   3. else plain "newest wins" (the single-slot values).
            if self._active_browser is not None:
                # STRICT: only this browser, no fallback.
                entry = self._browser_readings.get(self._active_browser) or {}
                claude_real = dict(entry["claude"]) if isinstance(entry.get("claude"), dict) else {}
                gemini_real = dict(entry["gemini"]) if isinstance(entry.get("gemini"), dict) else {}
            else:
                source_id = self._default_source_id()
                if source_id is not None:
                    # Chrome (the default browser) is open: show ONLY its
                    # readings - strictly, like a manual pin. If Chrome has not
                    # synced a provider yet, that provider stays empty and shows
                    # the "no data" (X) battery rather than borrowing a stale
                    # number from another browser.
                    entry = self._browser_readings.get(source_id) or {}
                    claude_real = dict(entry["claude"]) if isinstance(entry.get("claude"), dict) else {}
                    gemini_real = dict(entry["gemini"]) if isinstance(entry.get("gemini"), dict) else {}
                else:
                    # No default browser present: plain "newest wins".
                    claude_real = dict(self._claude_real)
                    gemini_real = dict(self._gemini_real)
            counts      = dict(self._counts)
            limit_hits  = {k: self._limit_hit_active(k) for k in PROVIDER_LIMITS}
            chatgpt_estimate = self._chatgpt_merged()
            multi = {p: self._multi_source(p) for p in ("claude", "gemini")}

        result = []
        for key, limit in PROVIDER_LIMITS.items():
            if key == "claude" and claude_real.get("fiveHourPercent") is not None:
                percent = float(claude_real["fiveHourPercent"])
                result.append({
                    "provider":    PROVIDER_NAMES[key],
                    "key":         key,
                    "used":        round(percent),
                    "limit":       100,
                    "unit":        "percent",
                    "percent":     round(min(100.0, percent), 1),
                    "reset_label": claude_real.get("resetLabel"),
                    "known":       True,
                    "multi_source": multi.get("claude", False),
                })
                continue

            if key == "gemini" and gemini_real.get("currentPercent") is not None:
                percent = float(gemini_real["currentPercent"])
                result.append({
                    "provider":    PROVIDER_NAMES[key],
                    "key":         key,
                    "used":        round(percent),
                    "limit":       100,
                    "unit":        "percent",
                    "percent":     round(min(100.0, percent), 1),
                    "reset_label": gemini_real.get("resetLabel"),
                    "known":       True,
                    "multi_source": multi.get("gemini", False),
                })
                continue

            if key == "chatgpt":
                hit = limit_hits.get(key, False)
                if hit:
                    # Real limit banner fired - the one true signal.
                    result.append({
                        "provider": PROVIDER_NAMES[key], "key": key,
                        "used": 100, "limit": 100, "unit": "percent",
                        "percent": 100.0, "reset_label": None,
                        "known": True, "estimated": False,
                    })
                elif chatgpt_estimate.get("percent") is not None:
                    # No official %; show the extension's rolling-window estimate.
                    p = float(chatgpt_estimate["percent"])
                    result.append({
                        "provider": PROVIDER_NAMES[key], "key": key,
                        "used": chatgpt_estimate.get("used", round(p)),
                        "limit": chatgpt_estimate.get("limit", 100),
                        "unit": "percent",
                        "percent": round(min(100.0, p), 1),
                        "reset_label": None,
                        "known": True, "estimated": True,
                        "browsers": chatgpt_estimate.get("browsers", 1),
                    })
                else:
                    result.append({
                        "provider": PROVIDER_NAMES[key], "key": key,
                        "used": 0, "limit": 100, "unit": "percent",
                        "percent": 0.0, "reset_label": None,
                        "known": False, "estimated": False,
                    })
                continue

            used    = counts.get(key, 0)
            percent = round(min(100.0, used / limit * 100), 1) if limit else 0.0
            result.append({
                "provider":    PROVIDER_NAMES[key],
                "key":         key,
                "used":        used,
                "limit":       limit,
                "unit":        "messages",
                "percent":     percent,
                "reset_label": None,
                "known":       used > 0,
            })
        return result

    def _maybe_reset(self):
        today = str(date.today())
        if today != self._today:
            self._counts = {k: 0 for k in PROVIDER_LIMITS}
            self._today  = today
            self._save()

    def _save(self):
        try:
            self._counts_file.write_text(json.dumps({
                "date":   self._today,
                "counts": self._counts,
            }, indent=2))
        except Exception as e:
            log.warning("Could not save counts: %s", e)

    def _load(self):
        try:
            if self._counts_file.exists():
                data = json.loads(self._counts_file.read_text())
                if data.get("date") == str(date.today()):
                    self._counts = data.get("counts", self._counts)
        except Exception as e:
            log.warning("Could not load counts: %s", e)

    def _fire_callbacks(self):
        data = self.usage_data()
        with self._cb_lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(data)
            except Exception:
                pass
