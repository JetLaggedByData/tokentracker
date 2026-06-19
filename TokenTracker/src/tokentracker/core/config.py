"""
Config — non-secret settings persisted to ~/.tokentracker/config.json.

API keys are NOT stored here — they go through core.secrets (OS keyring).
config.json contains only: demo_mode, refresh intervals, budgets, UI prefs.
"""

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR  = Path.home() / ".tokentracker"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Non-secret defaults only — no API keys here
DEFAULTS = {
    "demo_mode":              True,
    "default_provider":       "m365",
    "refresh_interval":       300,
    "m365_tenant_id":         "common",
    "m365_monthly_limit":     300,
    "openai_monthly_budget":  20.0,
    "anthropic_monthly_budget": 20.0,
    "warn_at_percent":        70,
    "critical_at_percent":    90,
}

# Keys that used to live in config but now live in secrets store
_MIGRATED_TO_SECRETS = {
    "openai_api_key", "anthropic_api_key", "m365_client_id",
}


def load() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            # Silently drop any keys that snuck into config.json (migration)
            cleaned = {k: v for k, v in saved.items() if k not in _MIGRATED_TO_SECRETS}
            return {**DEFAULTS, **cleaned}
        except Exception:
            pass
    return dict(DEFAULTS)


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Strip any secrets before writing — they must not land in config.json
    safe = {k: v for k, v in cfg.items() if k not in _MIGRATED_TO_SECRETS}
    CONFIG_FILE.write_text(json.dumps(safe, indent=2))
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set(key: str, value: Any) -> None:
    if key in _MIGRATED_TO_SECRETS:
        raise ValueError(
            f"'{key}' is a secret — use core.secrets.store_key() not config.set()"
        )
    cfg = load()
    cfg[key] = value
    save(cfg)


def is_first_run() -> bool:
    """True if no providers have been configured yet."""
    from . import secrets
    return (
        not secrets.has_key("m365_client_id") and
        not secrets.has_key("openai_api_key") and
        not secrets.has_key("anthropic_api_key")
    )


def extension_setup_shown() -> bool:
    """True once we've shown the first-run extension setup wizard."""
    return bool(load().get("_extension_setup_shown", False))


def mark_extension_setup_shown() -> None:
    set("_extension_setup_shown", True)


def autostart_prompted() -> bool:
    """True once we've already tried to set up Start-with-Windows.

    Consumer Edition enables autostart automatically on the very first launch
    (so the taskbar battery is one-click - install, run once, done). We only
    attempt it once; after that the user is free to toggle it off via the tray
    menu and we won't re-enable it behind their back.
    """
    return bool(load().get("_autostart_prompted", False))


def mark_autostart_prompted() -> None:
    set("_autostart_prompted", True)
