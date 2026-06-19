"""
ui/setup.py - first-run setup wizard (tkinter).

A non-programmer's whole job here is: install the .exe, then click one button
per browser to add the extension. This window detects the browsers they have
and shows a row for each with an "Add extension" button. When the extension is
published to the web stores, the button opens the store page directly (true
one-click). Until then it opens short manual-install instructions so testing
still works.

Runs on its own daemon thread like the dashboard, so it never blocks the tray.
"""

import logging
import threading
import webbrowser

log = logging.getLogger(__name__)

BG, CARD_BG, CARD_BD = "#080b14", "#131c30", "#243049"
TEXT, MUTED, FAINT = "#e8ecf4", "#8896b3", "#5c678a"
ACCENT = "#4ade80"
BTN_BG, BTN_FG = "#1f6feb", "#ffffff"

# Per-browser manual-install hint, used when no store URL is set yet.
_MANUAL_HINT = {
    "Chrome":  "chrome://extensions - enable Developer mode - Load unpacked",
    "Edge":    "edge://extensions - enable Developer mode - Load unpacked",
    "Brave":   "brave://extensions - enable Developer mode - Load unpacked",
    "Firefox": "about:debugging - This Firefox - Load Temporary Add-on",
}


# The internal extensions page each browser uses for manual ("unpacked") load.
EXTENSIONS_PAGE = {
    "Chrome":  "chrome://extensions",
    "Edge":    "edge://extensions",
    "Brave":   "brave://extensions",
    "Firefox": "about:debugging#/runtime/this-firefox",
}


def _open_for_browser(b, status_setter=None):
    """If the extension is published, open its store page (one-click install).
    Otherwise we CANNOT reliably open a chrome://-style internal page from
    outside the browser (it opens a blank tab), so instead we copy that page's
    address to the clipboard and tell the user to paste it. status_setter, if
    given, is called with a short confirmation string. Never raises."""
    url = b.get("store_url") or ""
    try:
        if url:
            webbrowser.open(url)
            if status_setter:
                status_setter("Opening the store page...")
            return
        # No store URL yet (pre-publish): copy the internal page address.
        page = EXTENSIONS_PAGE.get(b["name"], "")
        if page:
            _copy_to_clipboard(page)
            if status_setter:
                status_setter(f"Copied \"{page}\" - paste it into {b['name']}'s address bar")
    except Exception as e:
        log.debug("Setup action failed for %s: %s", b.get("name"), e)


def _copy_to_clipboard(text):
    """Put text on the clipboard via a throwaway hidden Tk root (no extra deps)."""
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()      # required for the clipboard to persist after destroy
        r.destroy()
    except Exception as e:
        log.debug("Clipboard copy failed: %s", e)


class _SetupThread(threading.Thread):
    def __init__(self, browsers):
        super().__init__(daemon=True, name="setup-ui")
        self._browsers = browsers

    def run(self):
        try:
            import tkinter as tk
        except Exception as e:
            log.warning("tkinter unavailable for setup wizard: %s", e)
            return

        root = tk.Tk()
        root.title("TokenTracker - Setup")
        root.configure(bg=BG)
        root.geometry("420x460")
        root.minsize(380, 360)
        try:
            from .dashboard import _set_window_icon
            _set_window_icon(root)
        except Exception:
            pass

        tk.Label(root, text="Add the TokenTracker extension",
                 bg=BG, fg=TEXT, font=("Segoe UI Semibold", 14)).pack(pady=(18, 4))
        tk.Label(root,
                 text=("The desktop app is running. To see your usage, add the\n"
                       "browser extension. Click a button below for each browser\n"
                       "you use - no admin needed."),
                 bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="center").pack(pady=(0, 12))

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=18)

        if not self._browsers:
            tk.Label(body, text="No supported browsers detected.\n"
                               "Install Chrome, Edge, Brave, or Firefox first.",
                     bg=BG, fg=FAINT, font=("Segoe UI", 10), justify="center").pack(pady=30)
        else:
            for b in self._browsers:
                self._browser_row(body, b)

        # Pin-to-taskbar tip. Windows hides new tray icons in the overflow (^)
        # flyout by default and only the USER can pin one to the always-visible
        # taskbar - no app is allowed to do it for them. So we just tell them how.
        tip = tk.Frame(root, bg=CARD_BG, highlightbackground=CARD_BD, highlightthickness=1)
        tip.pack(fill="x", padx=18, pady=(10, 0))
        tk.Label(tip, text="Keep the battery on your taskbar",
                 bg=CARD_BG, fg=ACCENT, font=("Segoe UI Semibold", 9),
                 anchor="w").pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(tip,
                 text=("Windows tucks new icons into the hidden \u2303 area. To keep\n"
                       "TokenTracker visible: click the \u2303 arrow by the clock, then\n"
                       "drag the battery icon down onto the taskbar. (Or: Settings >\n"
                       "Personalization > Taskbar > Other system tray icons > TokenTracker > On.)"),
                 bg=CARD_BG, fg=MUTED, font=("Segoe UI", 8), justify="left",
                 anchor="w").pack(fill="x", padx=12, pady=(0, 10))

        tk.Label(root,
                 text=("You can reopen this anytime from the tray menu - "
                       "\"Set up extension\"."),
                 bg=BG, fg=FAINT, font=("Segoe UI", 8), justify="center").pack(side="bottom", pady=10)

        try:
            root.lift(); root.attributes("-topmost", True)
            root.after(400, lambda: root.attributes("-topmost", False))
        except Exception:
            pass
        root.mainloop()

    def _browser_row(self, parent, b):
        import tkinter as tk
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=CARD_BD, highlightthickness=1)
        card.pack(fill="x", pady=5)
        row = tk.Frame(card, bg=CARD_BG)
        row.pack(fill="x", padx=12, pady=10)
        tk.Label(row, text=b["name"], bg=CARD_BG, fg=TEXT,
                 font=("Segoe UI", 11)).pack(side="left")
        has_store = bool(b.get("store_url"))

        # A small status line under the row for copy confirmations / hints.
        status = tk.Label(card, text=("" if has_store else _MANUAL_HINT.get(b["name"], "")),
                          bg=CARD_BG, fg=FAINT, font=("Segoe UI", 8), anchor="w",
                          justify="left", wraplength=360)
        status.pack(fill="x", padx=12, pady=(0, 8))

        def set_status(msg):
            try:
                status.config(text=msg, fg=ACCENT)
            except Exception:
                pass

        # Published -> "Add extension" (opens store). Pre-publish -> "Copy address"
        # (copies chrome://extensions etc. to the clipboard; we can't open those
        # internal pages from outside the browser without a blank tab).
        label = "Add extension" if has_store else "Copy address"
        btn = tk.Button(row, text=label, bg=BTN_BG, fg=BTN_FG,
                        activebackground="#388bfd", activeforeground=BTN_FG,
                        relief="flat", font=("Segoe UI Semibold", 9), padx=12, pady=3,
                        cursor="hand2",
                        command=lambda bb=b, ss=set_status: _open_for_browser(bb, ss))
        btn.pack(side="right")


def show_setup(browsers=None):
    """Open the setup wizard. If browsers is None, detect them now."""
    if browsers is None:
        try:
            from ..core.browsers import detect_installed_browsers
            browsers = detect_installed_browsers()
        except Exception:
            browsers = []
    _SetupThread(browsers).start()
