"""
Windows platform utilities.

WP-02  DPI awareness — tells Windows this app is DPI-aware so it
       doesn't blur-scale the tray icon on HiDPI displays.
WP-06  Single instance — mutex prevents two copies running at once.
WP-07  Crash logging — writes uncaught exceptions to a log file the
       user can find and attach to a bug report.
"""

import logging
import os
import sys
import threading
from pathlib import Path

LOG_DIR  = Path.home() / ".tokentracker"
LOG_FILE = LOG_DIR / "tokentracker.log"

_mutex_handle = None   # keep alive for process lifetime


# ── WP-07: Logging ───────────────────────────────────────────────────────────

def setup_logging() -> None:
    """Configure logging to both file and stderr."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )

    # WP-07: catch ALL uncaught exceptions and log them before crash
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        logging.critical("Log file: %s", LOG_FILE)

    sys.excepthook = _excepthook

    # Also catch exceptions in non-main threads
    def _thread_excepthook(args):
        logging.critical(
            "Uncaught exception in thread %s", args.thread,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_excepthook


def set_app_user_model_id() -> None:
    """Give the process its own Windows taskbar identity.

    Without this, when TokenTracker runs under a shared host interpreter
    (e.g. `python.exe` during development) Windows groups it under Python and
    shows the Python logo in the taskbar. Setting an explicit AppUserModelID
    makes Windows treat it as its own app so the correct icon/grouping is used.
    No-op off Windows. (In the packaged .exe the icon is already the battery.)
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "AISUS.TokenTracker.Consumer.0_1")
    except Exception:
        pass


# ── WP-02: DPI awareness ─────────────────────────────────────────────────────

def set_dpi_aware() -> None:
    """
    Tell Windows this process is DPI-aware.
    Without this, Windows scales the tray icon by the DPI factor, producing
    a blurry result on 125% / 150% / 200% displays.

    Tries the modern Per-Monitor V2 API first (Win 10 1703+),
    falls back to SetProcessDPIAware (Vista+), silently ignores on non-Windows.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # Per-Monitor V2 — sharpest on mixed-DPI setups
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ── WP-06: Single instance ───────────────────────────────────────────────────

def acquire_single_instance_lock() -> bool:
    """
    Prevent two copies of TokenTracker running simultaneously.

    On Windows: creates a named mutex. Second instance finds it taken and exits.
    On other platforms: uses a lockfile with the process PID.

    Returns True if this is the only running instance, False if another exists.
    """
    if sys.platform == "win32":
        return _win32_mutex()
    return _lockfile()


def release_single_instance_lock() -> None:
    """Call on clean shutdown to release the lock."""
    if sys.platform == "win32":
        _win32_mutex_release()
    else:
        _lockfile_release()


def _win32_mutex() -> bool:
    global _mutex_handle
    try:
        import ctypes
        import ctypes.wintypes
        MUTEX_NAME = "TokenTracker_SingleInstance_Mutex"
        handle = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX_NAME)
        last_err = ctypes.windll.kernel32.GetLastError()
        ERROR_ALREADY_EXISTS = 183
        if last_err == ERROR_ALREADY_EXISTS:
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle   # keep reference alive
        return True
    except Exception:
        return True  # if we can't check, assume we're the only one


def _win32_mutex_release() -> None:
    global _mutex_handle
    if _mutex_handle:
        try:
            import ctypes
            ctypes.windll.kernel32.ReleaseMutex(_mutex_handle)
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        except Exception:
            pass
        _mutex_handle = None


_LOCK_FILE = LOG_DIR / "tokentracker.lock"


def _lockfile() -> bool:
    try:
        _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _LOCK_FILE.exists():
            pid = int(_LOCK_FILE.read_text().strip())
            try:
                os.kill(pid, 0)   # check if process alive
                return False      # alive — another instance running
            except (ProcessLookupError, PermissionError):
                pass              # dead process — stale lock, take it
        _LOCK_FILE.write_text(str(os.getpid()))
        return True
    except Exception:
        return True


def _lockfile_release() -> None:
    try:
        if _LOCK_FILE.exists():
            _LOCK_FILE.unlink()
    except Exception:
        pass
