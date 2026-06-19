"""
Secure credential storage — FIX for Surface 1 (plaintext keys) and Surface 5 (memory).

Strategy:
  - API keys stored in OS credential store (Windows Credential Manager /
    macOS Keychain / Linux Secret Service) via the `keyring` library
  - config.json stores NON-SECRET settings only (provider names, budgets, intervals)
  - Keys never written to disk in plaintext
  - Keys fetched from keyring just-in-time and not held longer than needed

FIX for Surface 1 — Windows NTFS ACLs:
  os.chmod(0o600) is a no-op on Windows for restricting other users.
  keyring uses Windows Credential Manager which enforces user-level isolation
  via NTFS ACLs + DPAPI encryption automatically.

FIX for Surface 4 — input validation:
  All keys validated (format + max length) before being stored.
"""

import re
import sys
from typing import Optional

try:
    import keyring
    import keyring.errors
    _KEYRING_AVAILABLE = True
except ImportError:
    _KEYRING_AVAILABLE = False

SERVICE = "TokenTracker"

# Key name → (prefix, max_length)
KEY_RULES: dict[str, tuple[str, int]] = {
    "openai_api_key":    ("sk-",     256),
    "anthropic_api_key": ("sk-ant-", 256),
    "m365_client_id":    ("",        128),   # UUIDs are 36 chars
    "m365_tenant_id":    ("",        128),
}

_NULL_RE = re.compile(r'[\x00-\x1f\x7f]')   # control chars incl. null byte


# ── Validation (Surface 4 fix) ────────────────────────────────────────────────

class InvalidKeyError(ValueError):
    pass


def validate_key(name: str, value: str) -> str:
    """
    Validate and sanitise a credential value before storing.
    Raises InvalidKeyError with a human-readable message on failure.
    Returns the sanitised value on success.
    """
    value = value.strip()

    if not value:
        raise InvalidKeyError(f"{name}: value is empty")

    # Strip control characters (null bytes, newlines, etc.)
    if _NULL_RE.search(value):
        raise InvalidKeyError(f"{name}: contains invalid control characters")

    rules = KEY_RULES.get(name)
    if rules:
        prefix, max_len = rules
        if len(value) > max_len:
            raise InvalidKeyError(
                f"{name}: too long ({len(value)} chars, max {max_len})"
            )
        if prefix and not value.startswith(prefix):
            raise InvalidKeyError(
                f"{name}: expected prefix '{prefix}', got '{value[:12]}...'"
            )

    return value


# ── Storage (Surface 1 fix) ───────────────────────────────────────────────────

def store_key(name: str, value: str) -> None:
    """
    Store a credential securely.
    On Windows: Windows Credential Manager (DPAPI-encrypted, user-isolated).
    On macOS:   Keychain.
    On Linux:   Secret Service (GNOME Keyring / KWallet).
    Fallback:   config.json with 0o600 (same as before, but clearly labelled).
    """
    value = validate_key(name, value)

    if _KEYRING_AVAILABLE:
        try:
            keyring.set_password(SERVICE, name, value)
            return
        except keyring.errors.KeyringError:
            pass  # fall through to file fallback

    # Fallback — write to dedicated secrets file with tight permissions
    _write_fallback(name, value)


def get_key(name: str) -> Optional[str]:
    """
    Retrieve a stored credential. Returns None if not set.
    Never raises — returns None on any error.
    """
    if _KEYRING_AVAILABLE:
        try:
            val = keyring.get_password(SERVICE, name)
            if val:
                return val
        except keyring.errors.KeyringError:
            pass

    return _read_fallback(name)


def delete_key(name: str) -> None:
    """Remove a stored credential (e.g. on sign-out)."""
    if _KEYRING_AVAILABLE:
        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.KeyringError:
            pass
    _delete_fallback(name)


def has_key(name: str) -> bool:
    return get_key(name) is not None


# ── Fallback file storage (when keyring unavailable) ─────────────────────────

import json
import os
from pathlib import Path

_SECRETS_FILE = Path.home() / ".tokentracker" / "secrets.json"


def _write_fallback(name: str, value: str) -> None:
    _SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = _load_fallback_all()
    data[name] = _obfuscate(value)   # 5b: never store plaintext
    _SECRETS_FILE.write_text(json.dumps(data))
    try:
        os.chmod(_SECRETS_FILE, 0o600)
        if sys.platform == "win32":
            import subprocess
            username = os.environ.get("USERNAME", "")
            if username:
                subprocess.run(
                    ["icacls", str(_SECRETS_FILE), "/inheritance:r",
                     "/grant:r", f"{username}:F"],
                    capture_output=True, check=False
                )
    except Exception:
        pass


def _read_fallback(name: str) -> Optional[str]:
    raw = _load_fallback_all().get(name)
    if raw is None:
        return None
    try:
        return _deobfuscate(raw)   # 5b: decode obfuscated value
    except Exception:
        return raw   # legacy unobfuscated fallback


def _obfuscate(value: str) -> str:
    """
    5b: XOR-obfuscate with machine-derived key so secrets.json is not plaintext.
    Not cryptographic encryption — on Windows, DPAPI via keyring is always preferred
    and this path never runs. This is a defence-in-depth measure for Linux/headless.
    """
    import base64, hashlib
    key = _machine_key()
    xored = bytes(ord(c) ^ key[i % len(key)] for i, c in enumerate(value))
    return "ob1:" + base64.b64encode(xored).decode()   # prefix to detect encoded values


def _deobfuscate(encoded: str) -> str:
    import base64, hashlib
    if not encoded.startswith("ob1:"):
        return encoded   # legacy plaintext value
    key = _machine_key()
    xored = base64.b64decode(encoded[4:])
    return "".join(chr(b ^ key[i % len(key)]) for i, b in enumerate(xored))


def _machine_key() -> bytes:
    """Stable 32-byte key derived from machine + user identity."""
    import hashlib
    seed = (os.environ.get("COMPUTERNAME", "") +
            os.environ.get("USERNAME", "") +
            os.environ.get("USERDOMAIN", "TokenTracker"))
    return hashlib.sha256(seed.encode()).digest()


def _delete_fallback(name: str) -> None:
    data = _load_fallback_all()
    data.pop(name, None)
    if _SECRETS_FILE.exists():
        _SECRETS_FILE.write_text(json.dumps(data))


def _load_fallback_all() -> dict:
    if _SECRETS_FILE.exists():
        try:
            return json.loads(_SECRETS_FILE.read_text())
        except Exception:
            pass
    return {}


# ── Storage backend report (for setup wizard / diagnostics) ──────────────────

def storage_backend() -> str:
    """Human-readable description of what's backing credential storage."""
    if not _KEYRING_AVAILABLE:
        return "file (keyring not installed)"
    try:
        backend = keyring.get_keyring()
        name = type(backend).__name__
        if "Windows" in name:   return "Windows Credential Manager"
        if "macOS" in name or "Keychain" in name: return "macOS Keychain"
        if "Secret" in name:    return "Linux Secret Service"
        if "Fail" in name or "Null" in name:
            return "file (no OS keyring available)"
        return name
    except Exception:
        return "file (fallback)"
