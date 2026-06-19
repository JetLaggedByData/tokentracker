"""
Native desktop dashboard window (tkinter) showing every provider's usage as a
battery bar — Claude / Gemini / ChatGPT — with percentage, reset time, and a
source label. A real OS window with its own taskbar entry, not a browser tab.
tkinter ships with CPython, so there is no extra dependency.

Threading: a tray app's menu callback must not block (mainloop() would freeze
the tray). So the tkinter root lives on its OWN daemon thread with its own
event loop, created on first open. Subsequent opens just re-show and refresh
that window via a thread-safe `after()` call. Only one window ever exists.
"""

import logging
import queue
import threading

log = logging.getLogger(__name__)

BG, CARD_BG, CARD_BD = "#080b14", "#131c30", "#243049"
TEXT, MUTED, FAINT, TRACK = "#e8ecf4", "#8896b3", "#5c678a", "#1d2740"
BRAND = {"claude": "#CC785C", "gemini": "#4285F4", "chatgpt": "#10A37F"}
GREEN, AMBER, RED = "#00c878", "#e6a000", "#d22323"
BADGE_SYNC = "#4ade80"
BADGE_EST  = "#fbbf24"

# Hold a reference to the window-icon image for the process lifetime. tkinter
# does NOT keep its own reference to a PhotoImage, so without this the icon is
# garbage-collected and silently reverts to the default tk feather.
_icon_ref = None


# The provider whose battery the window icon mirrors (matches the tray's
# default). If it has no reading yet, we fall back to the first provider that
# does, then to a neutral battery.
DEFAULT_ICON_PROVIDER = "claude"


def _icon_provider(usage_list):
    """Pick which provider's reading the window icon should reflect: the
    default provider if it has a known reading, else the first one that does."""
    by_key = {d.get("key"): d for d in (usage_list or [])}
    pref = by_key.get(DEFAULT_ICON_PROVIDER)
    if pref and pref.get("known", True) and pref.get("percent") is not None:
        return pref
    for d in (usage_list or []):
        if d.get("known", True) and d.get("percent") is not None:
            return d
    return pref  # may be None / unknown - handled by the caller


def _icon_signature(usage_list):
    """A small tuple capturing only what the window icon depicts, so we can
    skip redundant iconphoto calls (which flicker on Windows)."""
    d = _icon_provider(usage_list)
    if d is None:
        return ("none",)
    known = d.get("known", True) and d.get("percent") is not None
    return (d.get("key"), known, bool(d.get("estimated")),
            None if not known else int(round(float(d.get("percent") or 0))))


def _set_window_icon(root, usage_list=None):
    """Replace tk's default feather icon with the TokenTracker battery, drawn to
    reflect the DEFAULT provider's current usage (same battery the tray shows).

    Renders via ui/icon.py into an in-memory PhotoImage applied with iconphoto -
    no file on disk, so it works identically in dev and in a frozen build. Falls
    back to the bundled .ico, then degrades silently; a wrong icon must never
    crash the window.
    """
    global _icon_ref
    try:
        from PIL import ImageTk
        from .icon import render_tray_icon, brand_color_for, BRAND_DEFAULT
        d = _icon_provider(usage_list)
        if d is not None:
            known = d.get("known", True) and d.get("percent") is not None
            pct = float(d.get("percent") or 0)
            border = brand_color_for(d.get("provider", "") or d.get("key", ""))
            img = render_tray_icon(percent=pct, is_unknown=not known,
                                   estimated=bool(d.get("estimated")),
                                   size=64, border_color=border)
        else:
            # No data yet - show an empty/unknown battery rather than a fake %.
            img = render_tray_icon(percent=0, is_unknown=True, size=64,
                                   border_color=BRAND_DEFAULT)
        _icon_ref = ImageTk.PhotoImage(img)
        root.iconphoto(True, _icon_ref)
        return
    except Exception as e:
        log.debug("iconphoto failed, trying .ico fallback: %s", e)
    try:
        import os
        ico = os.path.join(os.path.dirname(__file__), "..", "..", "..", "tokentracker.ico")
        ico = os.path.abspath(ico)
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception as e:
        log.debug("Could not set window icon: %s", e)


def _status_color(percent):
    if percent is None:
        return MUTED
    if percent >= 80:
        return RED
    if percent >= 60:
        return AMBER
    return GREEN


def _source_line(d):
    key = d.get("key", "")
    if not d.get("known", True):
        if key == "chatgpt":
            return "No messages yet this window · OpenAI shows no usage %"
        return "No data yet — visit the site to sync"
    if d.get("estimated"):
        used, lim = d.get("used"), d.get("limit")
        browsers = d.get("browsers", 1)
        suffix = f" · {browsers} browsers" if browsers and browsers > 1 else ""
        if used is not None and lim:
            return f"Estimated · {used}/{lim} msgs{suffix} (no official %)"
        return "Estimated usage"
    if d.get("multi_source"):
        return "Multiple browsers reporting · showing newest (may be different accounts)"
    return "Live reading from your account"


class _DashboardThread(threading.Thread):
    """Runs the tkinter root on its own thread; accepts refresh requests."""

    def __init__(self, usage_list, date_str):
        super().__init__(daemon=True, name="dashboard-ui")
        self._pending = (usage_list, date_str)
        self._q = queue.Queue()
        self._root = None
        self._cards = None
        self._date_lbl = None
        self._alive = False
        self._icon_sig = None   # last drawn window-icon signature

    # public (called from any thread)
    def request_show(self, usage_list, date_str, raise_window=True):
        # raise_window=True  -> bring the window to the front (user clicked "Show")
        # raise_window=False -> just refresh the data in place (live update; don't
        #                       steal focus while the user is doing something else)
        self._q.put((usage_list, date_str, raise_window))

    def is_alive_window(self):
        return self._alive

    def run(self):
        try:
            import tkinter as tk
        except Exception as e:
            log.warning("tkinter unavailable: %s", e)
            return

        root = tk.Tk()
        self._root = root
        self._alive = True
        root.title("TokenTracker")
        _set_window_icon(root, self._pending[0])
        root.configure(bg=BG)
        root.geometry("360x430")
        root.minsize(320, 300)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            root.attributes("-topmost", True)
            root.after(500, lambda: root.attributes("-topmost", False))
        except Exception:
            pass

        self._build_chrome(root)
        self._render(*self._pending)
        self._poll_queue()

        try:
            root.lift(); root.focus_force()
        except Exception:
            pass
        root.mainloop()
        self._alive = False

    def _on_close(self):
        # Hide instead of destroy so reopening is instant; destroy ends the loop.
        try:
            self._root.withdraw()
        except Exception:
            pass

    def _poll_queue(self):
        try:
            while True:
                usage_list, date_str, raise_window = self._q.get_nowait()
                self._render(usage_list, date_str)
                if raise_window:
                    try:
                        self._root.deiconify(); self._root.lift(); self._root.focus_force()
                    except Exception:
                        pass
        except queue.Empty:
            pass
        if self._root is not None:
            self._root.after(300, self._poll_queue)

    def _build_chrome(self, root):
        import tkinter as tk
        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(header, text="TokenTracker", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 13)).pack(side="left")
        self._date_lbl = tk.Label(header, text="", bg=BG, fg=MUTED, font=("Consolas", 9))
        self._date_lbl.pack(side="right")
        self._cards = tk.Frame(root, bg=BG)
        self._cards.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        tk.Label(root,
                 text=("Estimated usage based on your activity. Not affiliated with\n"
                       "OpenAI, Anthropic, or Google. Providers may change limits."),
                 bg=BG, fg=FAINT, font=("Segoe UI", 8), justify="center"
                 ).pack(side="bottom", pady=(0, 10))

    def _render(self, usage_list, date_str):
        import tkinter as tk
        if self._cards is None:
            return
        try:
            self._date_lbl.config(text=date_str)
        except Exception:
            pass
        # Keep the title-bar / taskbar battery in sync with the dashboard, but
        # only redraw when the depicted value actually changes (avoids flicker).
        try:
            sig = _icon_signature(usage_list)
            if sig != self._icon_sig:
                self._icon_sig = sig
                _set_window_icon(self._root, usage_list)
        except Exception as e:
            log.debug("Window-icon refresh skipped: %s", e)
        for w in self._cards.winfo_children():
            w.destroy()

        for d in usage_list:
            known = d.get("known", True)
            percent = d["percent"] if known else None
            est = "~" if d.get("estimated") else ""
            reset = d.get("reset_label") or ""
            color = _status_color(percent)

            card = tk.Frame(self._cards, bg=CARD_BG, highlightbackground=CARD_BD,
                            highlightthickness=1)
            card.pack(fill="x", pady=5)

            top = tk.Frame(card, bg=CARD_BG)
            top.pack(fill="x", padx=13, pady=(11, 6))
            name_wrap = tk.Frame(top, bg=CARD_BG)
            name_wrap.pack(side="left")
            dot = tk.Canvas(name_wrap, width=10, height=10, bg=CARD_BG, highlightthickness=0)
            dot.pack(side="left", padx=(0, 8))
            dot.create_oval(1, 1, 9, 9, fill=BRAND.get(d.get("key"), "#3C5880"), outline="")
            tk.Label(name_wrap, text=d.get("provider", ""), bg=CARD_BG, fg=TEXT,
                     font=("Segoe UI", 11)).pack(side="left")

            # synced (real reading) vs estimated (ChatGPT) badge
            if d.get("estimated"):
                tk.Label(name_wrap, text="  estimated", bg=CARD_BG, fg=BADGE_EST,
                         font=("Segoe UI", 8)).pack(side="left")
            elif known:
                tk.Label(name_wrap, text="  synced", bg=CARD_BG, fg=BADGE_SYNC,
                         font=("Segoe UI", 8)).pack(side="left")

            pill = "—" if percent is None else (f"{est}{round(percent)}%"
                                                + (f"  ·  {reset}" if reset else ""))
            tk.Label(top, text=pill, bg=CARD_BG, fg=color, font=("Consolas", 10)).pack(side="right")

            bar = tk.Canvas(card, height=12, bg=CARD_BG, highlightthickness=0)
            bar.pack(fill="x", padx=13, pady=(0, 4))
            bar.bind("<Configure>",
                     lambda e, c=bar, p=percent, col=color: self._draw_bar(c, p, col))

            tk.Label(card, text=_source_line(d), bg=CARD_BG, fg=FAINT,
                     font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=13, pady=(0, 10))

    @staticmethod
    def _draw_bar(canvas, percent, color):
        canvas.delete("all")
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w <= 1:
            return
        canvas.create_rectangle(0, 0, w, h, fill=TRACK, outline="")
        if percent is not None and percent > 0:
            fw = max(h, int(w * min(percent, 100) / 100))
            canvas.create_rectangle(0, 0, fw, h, fill=color, outline="")


_thread = None
_lock = threading.Lock()


def is_open() -> bool:
    """True if a dashboard window is currently alive (thread-safe)."""
    with _lock:
        return (_thread is not None and _thread.is_alive()
                and _thread.is_alive_window())


def show_dashboard(usage_list, date_str):
    """Open the dashboard, or bring it to the front if already open.
    Raises/focuses the window (this is a user-initiated "Show")."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive() and _thread.is_alive_window():
            _thread.request_show(usage_list, date_str, raise_window=True)
            return
        _thread = _DashboardThread(usage_list, date_str)
        _thread.start()


def refresh_if_open(usage_list, date_str) -> None:
    """Push fresh data to the dashboard ONLY if it is already open.
    Does not open a new window and does not steal focus - used for live
    updates (e.g. switching source browser, new readings arriving)."""
    with _lock:
        if (_thread is not None and _thread.is_alive()
                and _thread.is_alive_window()):
            _thread.request_show(usage_list, date_str, raise_window=False)
