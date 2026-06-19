"""
core/server.py - the localhost HTTP transport layer (port 7734).

This module is a thin adapter: it accepts requests from the browser extension,
enforces the request-level security (loopback Host header, shared-secret token,
CORS to extension origins only, body size cap, rate limiting), and forwards the
payload to the Counter. It owns no usage state and no token lifecycle itself -
those live in counter.py and auth.py respectively.

Endpoints (all require a loopback Host; all but /health and /pair require the
token):
  GET  /health            liveness + "refresh requested" flag (token-exempt)
  GET  /pair              hand the IPC token to the local extension (token-exempt)
  GET  /counts            current daily counts + limits
  POST /count             increment a provider's daily count
  POST /reset             zero all counts
  POST /claude_usage      store a real Claude reading
  POST /gemini_usage      store a real Gemini reading
  POST /chatgpt_usage     merge a browser's ChatGPT estimate
  POST /limit_hit         set/clear a provider's limit-reached flag
  POST /sync              bulk-set counts from the extension
  POST /register_browser  announce a browser so it is selectable as a source
  POST /request_refresh   flag that the tray wants a fresh pull

Re-exports: Counter, PROVIDER_LIMITS, PROVIDER_NAMES, and IPC_TOKEN are
re-exported here so existing imports (main_consumer, tray_browser, tests) keep
working unchanged after the split into auth.py / counter.py.
"""

import json
import logging
import secrets as _secrets_mod
import threading
import time as _time_mod
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import auth
from .counter import Counter, PROVIDER_LIMITS, PROVIDER_NAMES, _CHATGPT_HIT_TTL

log = logging.getLogger(__name__)

# ── Token (owned by auth.py; re-exported for a stable public API) ─────────────
# Computed at module load so a test that patches HOME and reloads this module
# gets a fresh token under the temporary home. Tests also reference _TOKEN_FILE,
# _TOKEN_RE, and _get_or_create_token by these names.
_TOKEN_RE = auth.TOKEN_RE
_TOKEN_FILE = auth.token_file()
_get_or_create_token = auth.get_or_create_token
IPC_TOKEN = auth.get_or_create_token()

# ── Transport configuration ───────────────────────────────────────────────────
PORT = 7734

# Only these Host header values are accepted. A browser performing a
# DNS-rebinding attack reaches the server with the attacker's hostname in the
# Host header (e.g. "evil.com:7734"); rejecting anything that is not a literal
# loopback name neutralises that attack regardless of CORS.
_ALLOWED_HOSTS = {
    "127.0.0.1:7734", "localhost:7734", "[::1]:7734",
    "127.0.0.1", "localhost", "[::1]",
}

# Largest POST body we will read into memory (counts/usage payloads are tiny).
_MAX_BODY_BYTES = 64 * 1024

# Endpoints that carry real usage readings we must never drop. A burst could
# exceed a tight global limit and silently discard a valid reading, so these
# bypass the rate limiter. They are still token-gated and body-capped.
_RATE_EXEMPT_PATHS = {"/claude_usage", "/gemini_usage", "/chatgpt_usage",
                      "/limit_hit", "/sync", "/register_browser"}


class _RateLimiter:
    """Sliding-window limiter. The ceiling is deliberately well above any
    legitimate burst so it only trips on runaway/abusive callers."""
    def __init__(self, max_calls: int = 120, window: float = 10.0):
        self._max   = max_calls
        self._window = window
        self._calls = []
        self._lock  = threading.Lock()

    def allow(self) -> bool:
        now = _time_mod.time()
        with self._lock:
            self._calls = [t for t in self._calls if now - t < self._window]
            if len(self._calls) >= self._max:
                return False
            self._calls.append(now)
            return True

_rate_limiter = _RateLimiter()


def _make_handler(counter: "Counter"):

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, format, *args):
            pass

        def handle_one_request(self):
            # The browser extension uses very short fetch timeouts (1s) and its
            # MV3 service worker can be torn down mid-request, so the client
            # routinely drops the socket before we finish replying. That is
            # normal, not an error - swallow the disconnect instead of letting
            # socketserver print a multi-line traceback for every dropped poll.
            try:
                super().handle_one_request()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                self.close_connection = True
            except OSError:
                # Any other low-level socket error: end the connection quietly.
                self.close_connection = True

        def _send_json(self, code: int, data: dict):
            body = json.dumps(data).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type",  "application/json")
                self.send_header("Content-Length", str(len(body)))
                # CORS - only reflect browser-extension origins, never "*".
                origin = self.headers.get("Origin", "")
                if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-TokenTracker-Token")
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                # Client hung up before we finished - nothing to do, not an error.
                self.close_connection = True

        def do_OPTIONS(self):
            if not self._validate_host():
                self._send_json(421, {"error": "misdirected request"})
                return
            self._send_json(200, {})

        def _validate_host(self) -> bool:
            """Reject requests whose Host header is not a loopback name.
            This is the standard DNS-rebinding defence: a malicious website
            that rebinds its domain to 127.0.0.1 still sends Host: evil.com,
            so it never matches and cannot reach any endpoint (including
            /pair). Binding to 127.0.0.1 only stops remote IPs; this stops
            the browser-based bypass."""
            host = (self.headers.get('Host') or '').lower()
            return host in _ALLOWED_HOSTS

        def _validate_token(self) -> bool:
            # /health and /pair are the only token-exempt paths (they still
            # require a valid loopback Host via _validate_host). /pair hands
            # the token to the extension on first run and after an MV3
            # service-worker restart drops the cached token.
            if self.path in ("/health", "/pair"):
                return True
            tok = self.headers.get("X-TokenTracker-Token", "")
            return _secrets_mod.compare_digest(tok, IPC_TOKEN)

        def _check_rate(self) -> bool:
            return _rate_limiter.allow()

        def do_GET(self):
            if not self._validate_host():
                self._send_json(421, {"error": "misdirected request"})
                return
            if not self._validate_token():
                self._send_json(403, {"error": "invalid token"})
                return
            if self.path == "/counts":
                self._send_json(200, {
                    "counts":   counter.get(),
                    "limits":   PROVIDER_LIMITS,
                    "names":    PROVIDER_NAMES,
                })
            elif self.path == "/health":
                # `refresh` true means the tray's "Refresh now" was clicked; the
                # extension polls this and pulls fresh readings when it sees it.
                self._send_json(200, {"ok": True, "port": PORT,
                                      "refresh": counter.consume_refresh()})
            elif self.path == "/pair":
                # First-run pairing: hand the IPC token to the local
                # extension. Reachable only with a loopback Host header
                # (see _validate_host), which blocks DNS-rebinding from
                # websites. NOTE: any process running as the same OS user
                # can still call this and read the token - that is an
                # accepted limitation; the token is not a defence against
                # same-user local code, only against other browser origins
                # and remote/ rebound callers. OS user isolation is the
                # real boundary for local processes.
                self._send_json(200, {"token": IPC_TOKEN})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if not self._validate_host():
                self._send_json(421, {"error": "misdirected request"})
                return
            if not self._validate_token():
                self._send_json(403, {"error": "invalid token"})
                return
            # Data-bearing endpoints bypass the limiter so a legitimate burst
            # never drops a real usage reading; everything else is throttled.
            if self.path not in _RATE_EXEMPT_PATHS and not self._check_rate():
                log.warning("Rate limit hit - dropping POST to %s", self.path)
                self._send_json(429, {"error": "rate limit exceeded"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send_json(400, {"error": "bad content-length"})
                return
            if length < 0 or length > _MAX_BODY_BYTES:
                self._send_json(413, {"error": "payload too large"})
                return
            body = self.rfile.read(length)

            if self.path == "/count":
                try:
                    data     = json.loads(body)
                    provider = data.get("provider", "")
                    counts   = counter.increment(provider)
                    self._send_json(200, {"ok": True, "counts": counts})
                except Exception as e:
                    self._send_json(400, {"error": str(e)})

            elif self.path == "/reset":
                counts = counter.reset()
                self._send_json(200, {"ok": True, "counts": counts})

            elif self.path == "/limit_hit":
                try:
                    data = json.loads(body)
                    counter.set_limit_hit(data["provider"], bool(data.get("hit", True)))
                    self._send_json(200, {"ok": True})
                except Exception as e:
                    self._send_json(400, {"error": str(e)})

            elif self.path == "/claude_usage":
                try:
                    data = json.loads(body)
                    counter.set_claude_real_usage(data)
                    self._send_json(200, {"ok": True})
                except Exception as e:
                    self._send_json(400, {"error": str(e)})

            elif self.path == "/gemini_usage":
                try:
                    data = json.loads(body)
                    counter.set_gemini_real_usage(data)
                    self._send_json(200, {"ok": True})
                except Exception as e:
                    self._send_json(400, {"error": str(e)})

            elif self.path == "/chatgpt_usage":
                try:
                    data = json.loads(body)
                    counter.set_chatgpt_estimate(data)
                    self._send_json(200, {"ok": True})
                except Exception as e:
                    self._send_json(400, {"error": str(e)})

            elif self.path == "/sync":
                try:
                    data   = json.loads(body)
                    synced = data.get("counts", {})
                    if isinstance(synced, dict):
                        counter.sync_from_extension(synced)
                    self._send_json(200, {"ok": True})
                except Exception as e:
                    self._send_json(400, {"error": str(e)})

            elif self.path == "/register_browser":
                try:
                    d = json.loads(body)
                    counter.register_browser(d.get("browserId"), d.get("browserName"))
                    self._send_json(200, {"ok": True})
                except Exception as e:
                    self._send_json(400, {"error": str(e)})

            elif self.path == "/request_refresh":
                # Tray -> server: flag that the user wants a fresh pull. The
                # extension acts on it via its /health poll. Body is ignored.
                counter.request_refresh()
                self._send_json(200, {"ok": True})

            else:
                self._send_json(404, {"error": "not found"})

    return Handler


class BrowserServer:
    def __init__(self, counter: "Counter"):
        self.counter = counter
        self._server = None
        self._thread = None

    def start(self) -> bool:
        """Start the HTTP server in a daemon thread. Returns True on success."""
        try:
            handler = _make_handler(self.counter)
            self._server = HTTPServer(("127.0.0.1", PORT), handler)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="browser-server",
            )
            self._thread.start()
            log.info("Browser server listening on http://127.0.0.1:%d", PORT)
            return True
        except OSError as e:
            log.error("Could not start browser server on port %d: %s", PORT, e)
            return False

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()   # release the listening socket
