"""
TokenTracker — Consumer Edition entry point.

For normal people who use Claude.ai, Gemini, and ChatGPT in their browser.
No API keys. No setup. Just install and the battery icon appears.

Data comes from the browser extension via localhost — the extension reads
each provider's real usage page and pushes the reading here in real time.
"""

import sys
import os

# Ensure the src/ package is importable regardless of install state
_here = os.path.dirname(os.path.abspath(__file__))
_src  = os.path.join(_here, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
from tokentracker.core.platform import (
    setup_logging, set_dpi_aware, set_app_user_model_id,
    acquire_single_instance_lock, release_single_instance_lock
)
from tokentracker.core.server import Counter, BrowserServer, IPC_TOKEN
from tokentracker.core.updater import check_for_updates
from tokentracker.ui.tray_browser import BrowserTrayApp

import logging
log = logging.getLogger(__name__)


def _log_token_ready() -> None:
    """
    The IPC token is generated and persisted by server.py on import. The
    extension obtains it at runtime from the tray app's localhost /pair
    endpoint (and re-pairs automatically if the token is ever regenerated),
    so there is nothing to push from here — we just confirm it exists.
    """
    from pathlib import Path
    token_file = Path.home() / ".tokentracker" / "ipc_token.txt"
    log.info("IPC token ready at %s (extension pairs via /pair)", token_file)


def main() -> None:
    # Platform setup — same as developer edition
    setup_logging()
    log.info("TokenTracker (consumer) starting — v0.1.0")
    set_dpi_aware()
    set_app_user_model_id()   # own taskbar identity (battery icon, not Python)

    if not acquire_single_instance_lock():
        log.warning("Already running — exiting")
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "TokenTracker is already running.\nCheck your system tray.",
                    "TokenTracker", 0x40
                )
            except Exception:
                pass
        sys.exit(0)

    # Counter — shared between server and tray
    counter = Counter()

    # Start localhost server (receives counts from browser extension)
    server  = BrowserServer(counter)
    started = server.start()

    if not started:
        log.warning(
            "Could not start localhost server on port 7734. "
            "Is another instance running? Continuing without browser counts."
        )

    # Build tray app
    app = BrowserTrayApp(counter, server)

    # Check for updates in background
    check_for_updates(on_update_available=lambda v, u: app.notify_update(v, u))

    # IPC token is on disk; the extension pairs to it via /pair at runtime
    _log_token_ready()

    try:
        app.run()
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        app.stop()
    except Exception as e:
        log.error("Tray app crashed: %s", e, exc_info=True)
        # Show error to user on Windows
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"TokenTracker failed to start:\n\n{e}\n\nCheck the log file:\n"
                    f"%USERPROFILE%\\.tokentracker\\tokentracker.log",
                    "TokenTracker Error", 0x10  # MB_ICONERROR
                )
            except Exception:
                pass
        raise
    finally:
        release_single_instance_lock()
        log.info("TokenTracker stopped")
        # Force-terminate. A dashboard/setup tkinter window runs its own native
        # event loop on a daemon thread; on Windows that loop can keep the
        # process alive past a normal sys.exit, leaving a ghost tray icon.
        # os._exit guarantees the process ends the instant the user clicks Quit.
        os._exit(0)


if __name__ == "__main__":
    main()
