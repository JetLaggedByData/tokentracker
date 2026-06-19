# Changelog

All notable changes to TokenTracker are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Fixed (Consumer Edition hardening)
- **IPC token seeding (consumer data path was broken under MV3).** The tray
  server requires a token on every request, but nothing delivered it to the
  extension, so after the first Manifest V3 service-worker restart every POST
  was rejected with 403 and the battery stopped updating. The server now
  exposes the token over a localhost-only `GET /pair` endpoint; the extension
  fetches it on first run and automatically re-pairs and retries when a
  request returns 403 (e.g. after a worker restart or token regeneration).
- **ChatGPT limit detection was sticky and over-broad.** A detected
  "limit reached" banner is now timestamped and expires after the ~3-hour
  rolling window in the server, badge, and popup, instead of pinning ChatGPT
  at 100% forever. Detection patterns now require genuine banner phrasing and
  only scan alert/banner/toast elements, so ordinary conversation mentioning
  "limit", "try again later", or "mini" no longer trips a false positive.
- **Token file unprotected on Windows.** `ipc_token.txt` is now hardened with
  `icacls` (matching `secrets.py`), validated as strict 64-char hex, and
  written via an exclusive-create to close a TOCTOU window.
- **CORS tightened** from `Access-Control-Allow-Origin: *` to reflecting only
  `chrome-extension://` / `moz-extension://` origins.
- **Request body size cap** (64 KB) and malformed `Content-Length` guard on
  all POST endpoints.
- **`usage_data()` race** removed by snapshotting shared state under the lock;
  callback list now guarded by its own lock.
- **Rate limiter** raised to 120 req/10s and data-bearing endpoints
  (`/claude_usage`, `/gemini_usage`, `/limit_hit`, `/sync`) exempted so a
  legitimate burst can never silently drop a real usage reading.
- **Tracker shutdown** now interrupts the refresh interval immediately via an
  Event instead of blocking up to 5 minutes in `time.sleep`.
- **Update checker** no longer polls a placeholder GitHub URL on every start;
  it is disabled until a real release URL is configured (`GITHUB_API_URL`).
- **Start-with-Windows** refuses to register a Run key when not running as the
  packaged `.exe` (avoids relaunching a bare interpreter on boot).
- Removed trailing null-byte corruption in `main_consumer.py` and `content.js`
  and stale "Copilot" references left after its removal.

### Changed
- **Redesigned the taskbar tray icon** for legibility at 16px. The old
  icon crammed a percentage label into the battery body and turned to an
  unreadable smudge at tray size. The new icon is a clean vertical battery
  whose fill HEIGHT shows the level (green->amber->red), with the provider
  brand colour as the outline/cap and no in-icon text. ChatGPT estimates
  render with a hollow fill; unknown shows a dash; errors show an X. The
  number stays in the tooltip and popup.

### Added
- **Desktop usage dashboard.** Clicking the tray icon (or "Show dashboard")
  opens a native dark window (its own taskbar entry) showing Claude, Gemini,
  and ChatGPT each as a battery bar with %, reset time, and a source label
  (live vs estimated). Built with tkinter (bundled with Python — no extra
  dependency, no local server); runs on its own UI thread so the tray never
  blocks, and a single window instance is reused across opens.
- **Cross-browser extension.** A universal manifest now runs on Brave, Chrome,
  Edge (Chromium service worker) and Firefox (MV3 background event page +
  `browser_specific_settings.gecko`), with a `src/compat.js` shim aliasing the
  `chrome.*`/`browser.*` globals. `build_extension.py` packages per-browser
  zips into `dist/`.
- **One-click taskbar setup.** Consumer Edition enables Start-with-Windows
  automatically on first launch (once, then user-toggleable), so the tray
  battery returns on every login. Added a real multi-resolution app icon
  (`tokentracker.ico`) wired into the PyInstaller spec.
- `tests/test_config_autostart.py` covering the first-run autostart flag.
- `tests/test_server.py` and `tests/test_tracker_updater.py` — 27 tests
  covering token handling, ChatGPT TTL expiry, `usage_data` semantics, the
  HTTP endpoints (`/pair`, token gate, CORS, body cap, `/count`), tracker
  shutdown responsiveness, and the updater no-op path.

### Planned
- Code signing certificate (eliminates SmartScreen warning)
- OpenAI usage API (currently estimated from billing endpoint)
- Anthropic usage API (currently local token counting until Anthropic ships endpoint)
- Settings UI (replaces manual JSON editing)
- Usage trend popup (daily/weekly/monthly chart)
- Auto-update notification
- Localisation / i18n: multi-currency display, RTL layout support, translated UI strings

---

## [0.1.0] — 2026-06-08

### Added
- Battery-style system tray icon showing AI token/cost usage
- Provider brand colour as battery border (Claude terracotta, ChatGPT green, M365 blue)
- Percentage text bold white inside battery body
- Next-refresh countdown in tooltip and right-click menu
- Three provider support: Microsoft 365 Copilot, OpenAI, Anthropic/Claude
- First-run setup wizard with browser-based credential flow
- Provider switcher in right-click menu (one icon, switchable default)
- Demo mode with realistic fake data — works without API keys

### Security
- API keys stored in OS credential store (Windows Credential Manager / macOS Keychain / Linux Secret Service) via `keyring` — never written to disk in plaintext
- `certifi` CA bundle pinned — prevents corporate TLS-inspection proxies from intercepting API calls
- Input validation on all credential fields (format, length, null bytes, path traversal)
- `config.json` contains zero secrets — safe to inspect or back up

### Platform (Windows)
- DPI awareness (Per-Monitor V2) — sharp icon at 125%/150%/200% display scaling
- Single-instance mutex — prevents duplicate tray icons
- Crash logging to `~/.tokentracker/tokentracker.log`
- Version info embedded in `.exe` (visible in Windows Properties)
- Uninstall documented (including Credential Manager cleanup)

### Fixed
- BUG-01: First update callback missed after tracker start
- BUG-02: Division by zero when monthly budget set to $0
- BUG-03: Tray fell back incorrectly when active provider not configured
- BUG-04: Setup wizard lost data when switching provider tabs
- BUG-05: Callback list not thread-safe under concurrent access
- BUG-07: Anthropic usage cache lost updates under concurrent API calls
- BUG-08: OpenAI cost truncated fractional cents (int() → round())

---

[Unreleased]: https://github.com/YOUR_USERNAME/tokentracker/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/YOUR_USERNAME/tokentracker/releases/tag/v0.1.0
