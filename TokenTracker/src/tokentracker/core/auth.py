"""
core/auth.py - the IPC shared-secret token.

The tray app and the browser extension authenticate every state-changing
request with a 256-bit token. This module owns the token's whole lifecycle:
create it once, validate any existing file, and lock the file down to the
current OS user. It deliberately knows nothing about HTTP - server.py uses
these helpers and decides how the token gates requests.

Note on testability: the token file path is resolved from Path.home() at call
time, so a test that patches HOME and re-runs get_or_create_token() gets an
isolated token under the temporary home.
"""

import os
import re
import secrets
import sys
from pathlib import Path

# A valid token is exactly 64 lowercase hex chars (token_hex(32) = 256 bits).
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


def token_file() -> Path:
    """Location of the persisted token. Resolved live so tests can patch HOME."""
    return Path.home() / ".tokentracker" / "ipc_token.txt"


def harden_token_file_permissions(path: Path) -> None:
    """Restrict the token file to the current user only.

    os.chmod(0o600) is a no-op on Windows, so on win32 we additionally use
    icacls to strip inheritance and grant the current user full control - the
    same approach core/secrets.py uses for its fallback secrets file. Without
    this, ipc_token.txt is readable by any local user, and since the token is
    the sole gate on /reset and /sync, that would let another local account
    wipe or inject usage counts.
    """
    try:
        os.chmod(path, 0o600)
        if sys.platform == "win32":
            import subprocess
            username = os.environ.get("USERNAME", "")
            if username:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r",
                     "/grant:r", username + ":F"],
                    capture_output=True, check=False,
                )
    except Exception:
        pass


def get_or_create_token() -> str:
    """Return the shared IPC token, creating and persisting it on first run.

    Only accepts an existing file whose contents are a well-formed 64-char hex
    token; anything malformed (empty, truncated, corrupted, or attacker-seeded)
    is discarded and regenerated rather than trusted.
    """
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            tok = path.read_text().strip()
        except Exception:
            tok = ""
        if TOKEN_RE.match(tok):
            harden_token_file_permissions(path)
            return tok
    tok = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(tok)
    except Exception:
        path.write_text(tok)
    harden_token_file_permissions(path)
    return tok
