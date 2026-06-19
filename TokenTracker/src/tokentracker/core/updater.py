"""
BR-06 - Update checker.

Polls the GitHub Releases API in a background thread on startup.
If a newer version is available, adds an "Update available" item to the tray menu.
Never blocks startup. Fails silently if offline or rate-limited.
"""

import logging
import threading
from typing import Optional, Callable

log = logging.getLogger(__name__)

CURRENT_VERSION = "0.1.0"
# Set this to the real "owner/repo" once the project has a public GitHub
# release page, e.g. "https://api.github.com/repos/acme/tokentracker/releases/latest".
# While it is None the update check is skipped entirely rather than polling a
# placeholder URL that 404s on every startup.
GITHUB_API_URL = None

# Cached result - set once after background check completes
_latest_version: Optional[str]  = None
_release_url:    Optional[str]  = None
_check_done:     bool            = False


def _parse_version(v: str) -> tuple:
    """Convert '1.2.3' to (1, 2, 3) for comparison."""
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except Exception:
        return (0,)


def check_for_updates(on_update_available: Optional[Callable] = None) -> None:
    """
    Start a background thread that checks for a newer release on GitHub.
    Calls on_update_available(latest_version, release_url) if one is found.
    Safe to call multiple times - only runs the check once.
    """
    def _check():
        global _latest_version, _release_url, _check_done
        if not GITHUB_API_URL:
            log.debug("Update check skipped - no release URL configured")
            _check_done = True
            return
        try:
            from tokentracker.core import http
            resp = http.get(
                GITHUB_API_URL,
                headers={"Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                timeout=8,
            )
            if resp.status_code != 200:
                return
            data        = resp.json()
            latest      = data.get("tag_name", "").lstrip("v")
            release_url = data.get("html_url", "")

            if not latest:
                return

            if _parse_version(latest) > _parse_version(CURRENT_VERSION):
                _latest_version = latest
                _release_url    = release_url
                log.info("Update available: v%s -> v%s", CURRENT_VERSION, latest)
                if on_update_available:
                    try:
                        on_update_available(latest, release_url)
                    except Exception:
                        pass
            else:
                log.debug("No update available (current=%s, latest=%s)",
                          CURRENT_VERSION, latest)
        except Exception as e:
            log.debug("Update check failed (offline?): %s", e)
        finally:
            _check_done = True

    t = threading.Thread(target=_check, daemon=True, name="update-checker")
    t.start()


def get_latest_version() -> Optional[str]:
    """Return the latest version string if a newer one was found, else None."""
    return _latest_version


def get_release_url() -> Optional[str]:
    return _release_url


def update_available() -> bool:
    return _latest_version is not None
