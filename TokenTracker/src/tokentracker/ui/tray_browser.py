"""
ui/tray_browser.py - tray app for the consumer edition.

Identical look to the developer tray (battery icon, brand border colours,
right-click menu, countdown) but data comes from the browser extension
via the localhost server instead of provider APIs.
"""

import logging
import os
import sys
import threading
import time
import webbrowser
from typing import Optional

import pystray
from pystray import MenuItem as item, Menu

from ..core.server import Counter, BrowserServer, PROVIDER_NAMES, PORT
from ..core.platform import LOG_FILE
from .icon import render_tray_icon, BRAND_CLAUDE, BRAND_OPENAI, BRAND_GEMINI, BRAND_M365, BRAND_DEFAULT
import tokentracker.ui.dashboard as _dashboard

APP_VERSION = "0.1.0"

# Provider key -> brand border colour
# Copilot deliberately removed - company/M365 accounts expose no personal
# usage signal at all, so there is nothing real to track here.
BRAND_COLORS = {
    "claude":  BRAND_CLAUDE,
    "gemini":  BRAND_GEMINI,
    "chatgpt": BRAND_OPENAI,
}

# Provider key -> portal URL (for "Open in browser" shortcut)
PORTAL_URLS = {
    "claude":  "https://claude.ai",
    "gemini":  "https://gemini.google.com",
    "chatgpt": "https://chat.openai.com",
}

# Default provider shown on the icon
DEFAULT_PROVIDER = "claude"


class BrowserTrayApp:
    def __init__(self, counter: Counter, server: BrowserServer):
        self.counter  = counter
        self.server   = server
        self._tray: Optional[pystray.Icon] = None
        self._active  = DEFAULT_PROVIDER           # which provider the icon shows
        self._data    = counter.usage_data()       # list of dicts
        self._update_version: Optional[str] = None
        self._update_url:     Optional[str] = None
        self._last_menu_sig = None   # cache to avoid slow menu rebuilds

    # -- public --

    def run(self) -> None:
        # Register for live updates from counter
        self.counter.on_change(self._on_update)

        # First launch: enable Start-with-Windows automatically so the taskbar
        # battery is truly one-click. Only attempted once; the user can toggle
        # it off afterwards via the tray menu.
        self._maybe_autostart_first_run()

        # First launch: open the extension setup wizard once, so a new user is
        # guided to add the browser extension (the one manual step browsers
        # require). Reopenable anytime from the tray menu. Only shown once.
        self._maybe_show_setup_first_run()

        # On startup, ask the extension to push its data ASAP so the dashboard
        # fills within seconds instead of waiting for the next periodic push.
        try:
            self.counter.request_refresh()
        except Exception:
            pass

        # Start tick thread (refreshes countdown every 60s)
        threading.Thread(target=self._tick, daemon=True, name="tray-tick").start()

        self._tray = pystray.Icon(
            "TokenTracker",
            icon=self._make_icon(),
            title=self._make_tooltip(),
            menu=self._make_menu(),
        )

        def setup(icon):
            icon.visible = True   # required on Windows to actually show the icon
            import logging
            logging.getLogger(__name__).info("Tray icon visible in system tray")

        self._tray.run(setup=setup)

    def stop(self) -> None:
        # Stop the localhost server first so its socket is released.
        try:
            self.server.stop()
        except Exception:
            pass
        # Hide the icon BEFORE stopping pystray. On Windows the tray often leaves
        # a "ghost" of the icon behind until the user hovers over it; setting it
        # invisible first makes Windows remove it immediately.
        if self._tray:
            try:
                self._tray.visible = False
            except Exception:
                pass
            try:
                self._tray.stop()
            except Exception:
                pass

    def notify_update(self, version: str, url: str) -> None:
        self._update_version = version
        self._update_url     = url
        self._render(rebuild_menu=True)

    # -- data --

    def _on_update(self, usage_list: list) -> None:
        self._data = usage_list
        # Rebuild the (slow) menu only when its CONTENTS would change - i.e. the
        # set of providers or the set of source browsers. Routine percentage
        # updates just do the light icon+tooltip refresh.
        sig = self._menu_signature()
        rebuild = (sig != self._last_menu_sig)
        self._last_menu_sig = sig
        self._render(rebuild_menu=rebuild)
        # Keep an OPEN dashboard live: any change (new reading, or the user
        # switching the source browser) is pushed straight to it without
        # reopening or stealing focus. If the dashboard isn't open, this no-ops.
        try:
            from datetime import datetime
            date_str = datetime.now().strftime("%a %d %b, %H:%M")
            _dashboard.refresh_if_open(self._data, date_str)
        except Exception as e:
            logging.getLogger(__name__).debug("Dashboard live refresh skipped: %s", e)

    def _menu_signature(self):
        # Include the DISPLAYED integer percent (and known/estimated state) per
        # provider, so the menu rebuilds when a shown number changes - but not on
        # every sub-1% tick. This keeps the "Show provider" submenu in sync with
        # the dashboard without reintroducing per-tick rebuild lag.
        rows = tuple(sorted(
            (u.get("key", ""),
             None if not u.get("known", True) else int(round(u.get("percent", 0))),
             bool(u.get("estimated")))
            for u in self._data))
        try:
            browsers = tuple(sorted(b["id"] for b in self.counter.list_source_browsers()))
        except Exception:
            browsers = ()
        return (rows, browsers, bool(self._update_version))

    def _current(self) -> Optional[dict]:
        """Return the usage dict for the active provider."""
        for u in self._data:
            if u["key"] == self._active:
                return u
        # Fallback - pick the one with the most usage
        if self._data:
            best = max(self._data, key=lambda u: u["used"])
            self._active = best["key"]
            return best
        return None

    # -- render --

    def _render(self, rebuild_menu: bool = False) -> None:
        """Light refresh by default: update only the icon + tooltip, which are
        cheap. Rebuilding the tray MENU is slow on Windows (pystray tears down
        and re-registers the native menu) and caused a visible lag on every
        usage tick / source switch - so we only do it when the menu's CONTENTS
        actually change (provider list, source-browser list, update item).
        pystray reads `menu` fresh each time the user opens it, so the menu
        always reflects current state on next open regardless."""
        if not self._tray:
            return
        self._tray.icon  = self._make_icon()
        self._tray.title = self._make_tooltip()
        if rebuild_menu:
            self._tray.menu = self._make_menu()

    def _make_icon(self) -> object:
        u = self._current()
        if u is None:
            return render_tray_icon(0, is_error=True, border_color=BRAND_DEFAULT)
        return render_tray_icon(
            percent=u["percent"],
            is_unknown=not u.get("known", True),
            estimated=u.get("estimated", False),
            border_color=BRAND_COLORS.get(u["key"], BRAND_DEFAULT),
        )

    def _make_tooltip(self) -> str:
        u = self._current()
        if not u:
            return "TokenTracker - open Claude, Gemini, or ChatGPT to start"

        nxt = self._reset_label()

        if not u.get("known", True):
            # No real signal yet (ChatGPT before any limit banner seen)
            return f"{u['provider']}  .  - * {nxt}"

        pct = u["percent"]
        est = "~" if u.get("estimated") else ""
        return f"{u['provider']}  .  {est}{pct:.0f}% * {nxt}"

    def _make_menu(self) -> Menu:
        u   = self._current()
        nxt = self._reset_label()

        if not u:
            status = "Open Claude, Gemini, or ChatGPT to start"
        elif not u.get("known", True):
            status = f"{u['provider']}  .  - * {nxt}"
        else:
            status = f"{u['provider']}  .  {u['percent']:.0f}% * {nxt}"

        portal  = PORTAL_URLS.get(self._active, "")
        upd_url = self._update_url

        # pystray rule: callbacks must have EXACTLY 0, 1, or 2 args.
        # Default args count toward co_argcount and cause ValueError.
        # Use factory functions (closures) to capture loop variables safely.

        def _make_switch(key):
            """Factory - returns a 2-arg callback that switches to `key`."""
            def cb(icon, menu_item):
                self._switch(key)
            return cb

        def _show_dashboard(icon, menu_item):
            self._show_dashboard()

        def _refresh_now(icon, menu_item):
            # Ask the extension (which polls the server) to pull fresh readings.
            try:
                self.counter.request_refresh()
            except Exception as e:
                logging.getLogger(__name__).warning("Refresh request failed: %s", e)

        def _open_chat(icon, menu_item):
            webbrowser.open(portal)

        def _open_log(icon, menu_item):
            self._open_log()

        def _setup_ext(icon, menu_item):
            self._show_setup()

        def _toggle_startup(icon, menu_item):
            self._toggle_startup()

        def _quit(icon, menu_item):
            self.stop()

        def _open_update(icon, menu_item):
            if upd_url:
                webbrowser.open(upd_url)

        def _make_source(bid):
            """Factory - 2-arg callback that locks the tray to browser `bid`
            (None = follow the newest reporting browser). The radio dot is
            drawn natively via each item's `checked` callable (queried live by
            pystray when the menu opens), so no menu rebuild is needed."""
            def cb(icon, menu_item):
                try:
                    self.counter.set_active_browser(bid)
                    self._render()   # light: just refresh the icon/tooltip
                except Exception as e:
                    logging.getLogger(__name__).warning("Source switch failed: %s", e)
            return cb

        def _source_checked(bid):
            """Live predicate for the radio dot. Marks the browser whose reading
            is ACTUALLY shown - the manual pin if set, otherwise the default
            browser (Chrome) when it's open. bid=None ('All - newest') is only
            checked when there's no pin AND no default browser in play, i.e. when
            we're truly following newest-wins."""
            def is_checked(menu_item):
                try:
                    return self.counter.get_effective_source() == bid
                except Exception:
                    return False
            return is_checked

        # Build switcher submenu. The active provider gets a native radio dot
        # via `checked` (queried live), so it stays correct without a rebuild.
        def _provider_checked(key):
            return lambda mi: self._active == key
        switcher_items = []
        for entry in self._data:
            label = f"{entry['provider']}  ({entry['percent']:.0f}% used)"
            switcher_items.append(
                item(label, _make_switch(entry["key"]),
                     checked=_provider_checked(entry["key"]), radio=True))

        # Assemble menu
        menu_items = [
            item(status,              None, enabled=False),
            item(f"Resets {nxt}",     None, enabled=False),
            Menu.SEPARATOR,
            item("Show dashboard", _show_dashboard, default=True),
            item("Refresh now", _refresh_now),
        ]

        if switcher_items:
            menu_items.append(item("Show provider", Menu(*switcher_items)))
            menu_items.append(Menu.SEPARATOR)

        # "Source browser": only shown when >1 browser is reporting. Lets the
        # user lock the tray to one browser instead of newest-wins flip-flop.
        try:
            sources = self.counter.list_source_browsers()
            active  = self.counter.get_active_browser()
        except Exception:
            sources, active = [], None
        if len(sources) > 1:
            src_items = [
                item("All - newest", _make_source(None),
                     checked=_source_checked(None), radio=True)
            ]
            for b in sources:
                src_items.append(
                    item(b["name"], _make_source(b["id"]),
                         checked=_source_checked(b["id"]), radio=True))
            menu_items.append(item("Source browser", Menu(*src_items)))
            menu_items.append(Menu.SEPARATOR)

        if portal:
            menu_items.append(item("Open chat", _open_chat))

        menu_items.append(item("Set up extension", _setup_ext))
        menu_items.append(item("Open log file", _open_log))
        menu_items.append(
            item(f"Start with Windows: {'ON' if self._startup_enabled() else 'OFF'}",
                 _toggle_startup)
        )

        if self._update_version:
            menu_items.append(item(f"Update v{self._update_version}", _open_update))

        menu_items += [
            Menu.SEPARATOR,
            item(f"TokenTracker v{APP_VERSION}", None, enabled=False),
            item("Quit", _quit),
        ]

        return Menu(*menu_items)

    # -- helpers --

    def _switch(self, key: str) -> None:
        self._active = key
        self._render(rebuild_menu=True)

    def _reset_label(self) -> str:
        """
        Reset countdown for Claude/Gemini/ChatGPT (rolling windows) - shows
        a window-based label since exact reset depends on when the oldest
        message ages out. Format is bare ("2h 46m") - no "in"/"resets"
        prefix, matches the compact pill style: "24% * 2h 46m".
        """
        if self._active == "claude":
            u = self._current()
            if u and u.get("reset_label"):
                return self._strip_prefix(u["reset_label"])
            return "<=5h"
        if self._active == "gemini":
            u = self._current()
            if u and u.get("reset_label"):
                return self._strip_prefix(u["reset_label"])
            return "<=5h"
        if self._active == "chatgpt":
            return "<=3h"
        return "-"

    @staticmethod
    def _strip_prefix(label: str) -> str:
        """Remove leading 'in '/'resets in '/'Resets ' so the pill stays bare."""
        for prefix in ("resets in ", "resets ", "in "):
            if label.lower().startswith(prefix):
                return label[len(prefix):]
        return label

    def _maybe_autostart_first_run(self) -> None:
        """Enable Start-with-Windows on the first launch only (one-click setup)."""
        if sys.platform != "win32":
            return
        try:
            import tokentracker.core.config as config
            if config.autostart_prompted():
                return
            config.mark_autostart_prompted()
            if not self._startup_enabled():
                self._toggle_startup()
        except Exception as e:
            logging.getLogger(__name__).warning("First-run autostart skipped: %s", e)

    def _startup_enabled(self) -> bool:
        """2a: check if startup shortcut exists."""
        if sys.platform != "win32":
            return False
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, "TokenTracker")
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def _toggle_startup(self) -> None:
        """2a: add or remove startup registry entry."""
        if sys.platform != "win32":
            return
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
            if self._startup_enabled():
                winreg.DeleteValue(key, "TokenTracker")
            else:
                import pathlib
                exe = pathlib.Path(sys.executable).parent / "TokenTracker.exe"
                if not exe.exists():
                    # Not a frozen build - sys.executable is the bare Python
                    # interpreter. Registering it as a Run entry would relaunch
                    # Python with no script on next boot, so refuse instead of
                    # writing a broken entry.
                    winreg.CloseKey(key)
                    logging.getLogger(__name__).warning(
                        "Start-with-Windows is only available in the packaged "
                        "build (TokenTracker.exe not found next to %s)", sys.executable)
                    return
                winreg.SetValueEx(key, "TokenTracker", 0, winreg.REG_SZ, str(exe))
            winreg.CloseKey(key)
            self._render()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Startup toggle failed: %s", e)

    def _maybe_show_setup_first_run(self) -> None:
        """Show the extension setup wizard once, on the very first launch."""
        try:
            import tokentracker.core.config as config
            if config.extension_setup_shown():
                return
            config.mark_extension_setup_shown()
            self._show_setup()
        except Exception as e:
            logging.getLogger(__name__).debug("First-run setup skipped: %s", e)

    def _show_setup(self) -> None:
        """Open the extension setup wizard (detects installed browsers)."""
        try:
            import tokentracker.ui.setup as _setup
            _setup.show_setup()
        except Exception as e:
            logging.getLogger(__name__).warning("Could not open setup wizard: %s", e)

    def _show_dashboard(self) -> None:
        """Open the full usage dashboard (all providers) as a styled window."""
        try:
            from datetime import datetime
            date_str = datetime.now().strftime("%a %d %b, %H:%M")
            _dashboard.show_dashboard(self._data, date_str)
        except Exception as e:
            logging.getLogger(__name__).warning("Could not open dashboard: %s", e)

    def _open_log(self) -> None:
        import subprocess
        try:
            if sys.platform == "win32":
                os.startfile(str(LOG_FILE))
            elif sys.platform == "darwin":
                subprocess.call(["open", str(LOG_FILE)])
            else:
                subprocess.call(["xdg-open", str(LOG_FILE)])
        except Exception:
            pass

    def _tick(self) -> None:
        """Background loop: refresh the icon + tooltip every 60s so the reset
        countdown ("2h 46m") stays current even when no new reading arrives.
        Light refresh only - no menu rebuild, so it never causes tray lag."""
        while True:
            time.sleep(60)
            try:
                self._render()
            except Exception as e:
                logging.getLogger(__name__).debug("Tick refresh skipped: %s", e)
