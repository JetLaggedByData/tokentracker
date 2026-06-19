"""
core/browsers.py - detect which browsers are installed (Windows), no admin.

Used by the first-run setup wizard to show the user a button per browser they
actually have ("Add the extension to Chrome", etc.). Detection only READS the
registry and well-known install paths, so it never needs elevation.

Each detected browser carries the store URL where its extension lives, so the
wizard can open the right "Add to <browser>" page. Chrome/Edge/Brave all accept
the Chrome Web Store listing; Firefox uses addons.mozilla.org. The URLs are
placeholders until the extension is published - update STORE_URLS then.
"""

import os
import sys

# Where each browser's users install extensions from. Chromium browsers share
# the Chrome Web Store listing. Fill these in once the extension is published.
STORE_URLS = {
    "Chrome":  "",   # e.g. https://chromewebstore.google.com/detail/<id>
    "Edge":    "",   # Edge can install from the Chrome Web Store listing too
    "Brave":   "",   # Brave uses the Chrome Web Store listing
    "Firefox": "",   # e.g. https://addons.mozilla.org/firefox/addon/<slug>
}

# Detection recipe per browser:
#   reg_app_paths: the executable name under
#     HKLM/HKCU \Software\Microsoft\Windows\CurrentVersion\App Paths\<exe>
#   rel_paths: install locations relative to common base dirs, as a fallback.
_BROWSERS = [
    ("Chrome",  "chrome.exe",  [r"Google\Chrome\Application\chrome.exe"]),
    ("Edge",    "msedge.exe",  [r"Microsoft\Edge\Application\msedge.exe"]),
    ("Brave",   "brave.exe",   [r"BraveSoftware\Brave-Browser\Application\brave.exe"]),
    ("Firefox", "firefox.exe", [r"Mozilla Firefox\firefox.exe"]),
]


def _base_dirs():
    """Common install roots, both 64- and 32-bit, plus per-user."""
    dirs = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "ProgramW6432"):
        v = os.environ.get(env)
        if v:
            dirs.append(v)
    return dirs


def _exe_in_app_paths(exe_name):
    """Look up an executable in the Windows 'App Paths' registry, HKCU then
    HKLM. Returns the path string if present, else None. Read-only, no admin."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except Exception:
        return None
    sub = r"Software\Microsoft\Windows\CurrentVersion\App Paths\\" + exe_name
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, sub) as k:
                val, _ = winreg.QueryValueEx(k, None)  # default value = full path
                if val:
                    return val
        except OSError:
            continue
    return None


def _exe_on_disk(rel_paths):
    """Check standard install locations for one of the relative paths."""
    for base in _base_dirs():
        for rel in rel_paths:
            p = os.path.join(base, rel)
            if os.path.exists(p):
                return p
    return None


def detect_installed_browsers():
    """Return a list of {name, path, store_url} for browsers found on this
    machine. Order follows _BROWSERS (Chrome first). Empty list off Windows or
    if none are found. Never raises - detection failures just omit a browser."""
    found = []
    for name, exe, rel_paths in _BROWSERS:
        try:
            path = _exe_in_app_paths(exe) or _exe_on_disk(rel_paths)
        except Exception:
            path = None
        if path:
            found.append({
                "name": name,
                "path": path,
                "store_url": STORE_URLS.get(name, ""),
            })
    return found
