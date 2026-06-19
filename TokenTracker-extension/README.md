# TokenTracker

> See how much of your daily AI limit you have left — Claude, Gemini, and ChatGPT.

No account. No API key. Just install and go.

Works on **Brave, Chrome, Edge, and Firefox** from a single universal build.

---

## Install (30 seconds)

The same extension folder loads on every supported browser. Until it's
published on the stores, load it manually:

### Brave / Chrome / Edge
1. Download and unzip `dist/tokentracker-chromium.zip` (or use the
   `TokenTracker-extension` folder directly).
2. Open `chrome://extensions` (Chrome/Brave) or `edge://extensions` (Edge).
3. Turn on **Developer mode**.
4. Click **Load unpacked** and select the `TokenTracker-extension` folder.
5. Look for the battery icon next to the address bar.

### Firefox
1. Download `dist/tokentracker-firefox.zip` (or use the folder directly).
2. Open `about:debugging#/runtime/this-firefox`.
3. Click **Load Temporary Add-on…**.
4. Select the `manifest.json` inside the `TokenTracker-extension` folder.
5. Look for the battery icon in the toolbar.

> Firefox "temporary" add-ons unload when you close the browser. For a
> permanent install, the extension needs to be signed and distributed through
> addons.mozilla.org (AMO) — that's a publishing step, not a code change.

---

## How it works

Visit **claude.ai**, **gemini.google.com**, or **chatgpt.com** and start chatting.

- **Claude & Gemini** publish a real usage percentage on their own settings
  pages. TokenTracker reads that exact number (calculated server-side across
  all your devices) — it does not guess.
- **ChatGPT** exposes no official usage percentage anywhere. TokenTracker
  shows an **estimate** (marked with a `~`): it counts the messages you send
  in a rolling 3-hour window against ~160 (OpenAI's GPT-5.5 Go/Plus cap) and
  shows the percentage used. If ChatGPT's real "you've reached your limit"
  banner fires, that overrides the estimate with a definitive 100%. The
  estimate is local-only and may differ from OpenAI's internal counter
  (e.g. messages sent on mobile are not counted).

Click the battery icon to see each provider's usage bar and reset countdown:
🟢 green = plenty left · 🟡 amber = getting low · 🔴 red = almost out.

If the desktop tray app (battery in the system tray / taskbar) is running, the
extension also sends it the reading so the taskbar icon stays in sync.

---

## How sync works (extension + desktop app)

The browser extension is the data source; the desktop app is a mirror of it.

```
claude.ai / gemini.google.com        the extension reads the real % off the page
chatgpt.com  (messages you send)     (ChatGPT is estimated from your message count)
        |
        v
  Browser extension  ──POST──>  http://127.0.0.1:7734  (desktop app, your machine only)
        |                                   |
   popup shows it                     tray icon + dashboard show the same numbers
```

- Sync is **one-directional**: browser extension → desktop app. The desktop
  app never reads the websites itself; it only displays what the extension
  sends it.
- They stay in step **only while both are running** on the **same computer**.
  If the desktop app is closed, the extension keeps working on its own and
  retries the connection later. If the extension isn't installed (or you're
  not on the AI sites), the desktop app shows "No data yet".
- This is **local-only sync, not cloud sync.** There is no account and nothing
  leaves your machine, so usage does **not** sync across your devices — your
  laptop won't see usage from your phone, and two computers track separately.
- **Designed for one account used across your browsers.** The app can't tell
  which provider account a browser is signed into (it never reads your login).
  If you use the *same* account in Brave and Edge, ChatGPT message counts pool
  correctly. If you're signed into *different* accounts per browser, those
  counts are still combined — which will be inaccurate — and the desktop
  dashboard notes how many browsers contributed so you can spot it.
- First connection is automatic: the extension pairs with the desktop app over
  localhost the first time both are running (you may see a one-time browser
  prompt to allow access to `127.0.0.1`).

---

## Browser support

| Browser | Engine | Status |
|---|---|---|
| Chrome | Chromium | ✅ uses the MV3 service worker |
| Brave  | Chromium | ✅ uses the MV3 service worker |
| Edge   | Chromium | ✅ uses the MV3 service worker |
| Firefox | Gecko   | ✅ uses the MV3 background event page |

The manifest declares both a `service_worker` (Chromium) and a
`background.scripts` event page (Firefox); each browser picks the form it
supports. `src/compat.js` aliases the `chrome.*` / `browser.*` globals so the
same code runs everywhere.

---

## Privacy

- Usage readings come from each provider's own page; TokenTracker only reads
  the number, never your conversations.
- The only network call is to `http://127.0.0.1:7734` — the optional local
  desktop app on your own machine. Nothing is sent to any external server.

---

## Uninstall

- Brave/Chrome/Edge: `chrome://extensions` / `edge://extensions` → Remove.
- Firefox: `about:addons` → Remove (or just restart, if loaded temporarily).

No files left behind.
