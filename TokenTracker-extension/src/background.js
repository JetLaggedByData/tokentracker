// background.js — service worker (Manifest V3)
//
// Auto-detection only, no manual settings:
//   Claude:  real percentage read from claude.ai/settings/usage
//   Gemini:  real percentage read from gemini.google.com/usage
//   ChatGPT: "limit reached" banner detection (written directly to
//            storage by content.js — this file just reads it back
//            out for the badge)

// ── 3b: IPC token ─────────────────────────────────────────────────────────────
// The tray app generates a 256-bit token and requires it on every request.
// Under Manifest V3 the service worker is torn down when idle, so the cached
// token must be recoverable at any time — we read it from chrome.storage and,
// if absent (first run or freshly-woken worker), fetch it from the tray's
// localhost /pair bootstrap endpoint. fetchTray() below also re-pairs and
// retries automatically on a 403, so a regenerated token self-heals.
const TRAY_BASE = "http://127.0.0.1:7734";

// Poll the tray's /health for a "refresh requested" flag (set by the tray's
// "Refresh now" menu). When seen, pull fresh readings. /health is unauthenticated
// and tiny; the flag auto-clears server-side on read. No tabs unless requested.
async function checkTrayRefresh() {
  try {
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 1000);
    const resp = await fetch(`${TRAY_BASE}/health`, { signal: ctrl.signal });
    if (!resp.ok) return;                 // desktop app not running
    const data = await resp.json();
    // The desktop app is reachable -> mirror our known readings to it every
    // poll (~30s). This makes a freshly-started desktop app fill in quickly
    // instead of waiting up to the 5-min push. It's all cheap localhost POSTs,
    // no tabs.
    await registerBrowser();
    await pushAllKnownReadings();
    // Only an explicit "Refresh now" opens a real tab for a fresh page read.
    if (data && data.refresh) {
      _lastClaudeCheck = 0; _lastGeminiCheck = 0;   // bypass throttle
      checkClaudeUsage();
      checkGeminiUsage();
    }
  } catch (_) {}
}
let _ipcToken = "";

async function getIpcToken() {
  if (_ipcToken) return _ipcToken;
  const data = await chrome.storage.local.get("ipcToken");
  if (data.ipcToken) {
    _ipcToken = data.ipcToken;
    return _ipcToken;
  }
  return await pairWithTray();
}

// First-run / recovery pairing: ask the local tray app for the token.
// Reachable only because the server binds to 127.0.0.1.
async function pairWithTray() {
  try {
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 1000);
    const resp = await fetch(`${TRAY_BASE}/pair`, { signal: ctrl.signal });
    if (!resp.ok) return "";
    const { token } = await resp.json();
    if (token && /^[0-9a-f]{64}$/.test(token)) {
      _ipcToken = token;
      await chrome.storage.local.set({ ipcToken: token });
      return token;
    }
  } catch (_) {}
  return "";
}

function ipcHeaders(token, extra = {}) {
  return { "Content-Type": "application/json", "X-TokenTracker-Token": token, ...extra };
}

// Authenticated POST to the tray app. Resolves the token first, and if the
// server rejects it (403 — e.g. the token was regenerated), re-pairs once
// and retries. All failures are swallowed: the tray app is optional.
async function fetchTray(path, payload) {
  let token = await getIpcToken();
  const attempt = async (tok) => {
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 1000);
    return fetch(`${TRAY_BASE}${path}`, {
      method: "POST",
      headers: ipcHeaders(tok),
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
  };
  try {
    let resp = await attempt(token);
    if (resp && resp.status === 403) {
      _ipcToken = "";
      await chrome.storage.local.remove("ipcToken");
      token = await pairWithTray();
      if (token) await attempt(token);
    }
  } catch (_) {}
}

const PROVIDER_META = {
  claude:  { name: "Claude",  color: "#CC785C" },
  gemini:  { name: "Gemini",  color: "#4285F4" },
  chatgpt: { name: "ChatGPT", color: "#10A37F" },
};

// ── Badge — shows highest-usage provider's status ───────────────────────────

// ChatGPT's limit is a ~3h rolling window — a detected hit expires after this.
const CHATGPT_HIT_TTL_MS = 3 * 60 * 60 * 1000;

function chatgptHitActive(chatgptLimitHit) {
  if (!chatgptLimitHit?.hit) return false;
  const at = chatgptLimitHit.detectedAt || 0;
  return (Date.now() - at) < CHATGPT_HIT_TTL_MS;
}

// --- ChatGPT ESTIMATED usage (no official % exists) -------------------------
// OpenAI publishes no usage percentage for consumer ChatGPT. Per the OpenAI
// Help Center, ChatGPT Plus and Go users get 160 messages with GPT-5.5 per
// rolling 3-hour window (verified 2026-06; OpenAI changes this over time and
// per plan, so treat it as a default, not gospel). We count the messages the
// user sends and estimate % of that window used. This is explicitly an
// ESTIMATE and labelled as such in the UI. Override at runtime by setting
// chatgptLimit / chatgptWindowMs in chrome.storage.local.
// Source: https://help.openai.com/en/articles/11909943
const CHATGPT_DEFAULT_LIMIT  = 160;
const CHATGPT_WINDOW_MS      = 3 * 60 * 60 * 1000;

async function chatgptConfig() {
  const { chatgptLimit, chatgptWindowMs } = await chrome.storage.local.get(
    ["chatgptLimit", "chatgptWindowMs"]);
  return {
    limit:    Number.isFinite(chatgptLimit)    && chatgptLimit    > 0 ? chatgptLimit    : CHATGPT_DEFAULT_LIMIT,
    windowMs: Number.isFinite(chatgptWindowMs) && chatgptWindowMs > 0 ? chatgptWindowMs : CHATGPT_WINDOW_MS,
  };
}

// Record one sent message and prune timestamps outside the rolling window.
async function recordChatgptMessage(at) {
  const { windowMs } = await chatgptConfig();
  const now = Date.now();
  const { chatgptMsgTimes } = await chrome.storage.local.get("chatgptMsgTimes");
  const times = Array.isArray(chatgptMsgTimes) ? chatgptMsgTimes : [];
  times.push(typeof at === "number" ? at : now);
  const pruned = times.filter(t => now - t < windowMs);
  await chrome.storage.local.set({ chatgptMsgTimes: pruned });
  await updateBadge();
  await pushChatgptEstimate();
}

// Current rolling-window estimate: {percent, used, limit} or null if no data.
async function chatgptEstimate() {
  const { limit, windowMs } = await chatgptConfig();
  const now = Date.now();
  const { chatgptMsgTimes } = await chrome.storage.local.get("chatgptMsgTimes");
  const times = Array.isArray(chatgptMsgTimes) ? chatgptMsgTimes : [];
  const used = times.filter(t => now - t < windowMs).length;
  if (used === 0) return null;
  return { used, limit, percent: Math.min(100, Math.round(used / limit * 100)) };
}

// A stable per-browser id so the desktop app can pool ChatGPT activity from
// multiple browsers (Brave + Edge) instead of overwriting. Generated once.
async function browserId() {
  const { ttBrowserId } = await chrome.storage.local.get("ttBrowserId");
  if (ttBrowserId) return ttBrowserId;
  const id = (crypto.randomUUID && crypto.randomUUID()) ||
             (Date.now().toString(36) + Math.random().toString(36).slice(2));
  await chrome.storage.local.set({ ttBrowserId: id });
  return id;
}

// Best-effort human-readable browser name for the tray's "source browser"
// menu. Auto-detected from the user agent; the user can override it (stored
// as ttBrowserName). Falls back to "Browser" if detection is uncertain.
// The only names we trust as a stored override. A stored value that is NOT
// one of these is treated as stale/bogus (an older build mistakenly persisted
// the active tab's URL fragment such as "new"), so we ignore it and re-detect.
const KNOWN_BROWSER_NAMES = ["Firefox", "Edge", "Opera", "Vivaldi", "Brave", "Chrome"];

async function detectBrowserName() {
  const ua = (navigator.userAgent || "");
  // Order matters: every Chromium UA contains "Chrome", and Edge/Opera also
  // carry their own token, so check the specific browsers FIRST.
  try {
    if (/Firefox\//.test(ua))   return "Firefox";
    if (/Edg\//.test(ua))       return "Edge";      // Edge UA: "...Chrome/.. Edg/.."
    if (/OPR\/|Opera/.test(ua)) return "Opera";
    if (/Vivaldi/.test(ua))     return "Vivaldi";
    // Brave ships a plain Chrome UA; detect via its async probe.
    // navigator.brave.isBrave is a FUNCTION returning a Promise - must await.
    if (navigator.brave && typeof navigator.brave.isBrave === "function") {
      try { if (await navigator.brave.isBrave()) return "Brave"; } catch (_) {}
    }
    if (/Chrome\//.test(ua))    return "Chrome";
  } catch (_) {}
  return "Browser";
}

async function browserName() {
  const { ttBrowserName } = await chrome.storage.local.get("ttBrowserName");
  // Only honour a stored name if it is a real browser name. This self-heals a
  // stale/bogus value (e.g. "new") left by an older build: we discard it,
  // re-detect from the user agent, and overwrite the bad value in storage.
  if (ttBrowserName && KNOWN_BROWSER_NAMES.includes(ttBrowserName)) {
    return ttBrowserName;
  }
  const detected = await detectBrowserName();
  if (ttBrowserName && ttBrowserName !== detected) {
    try { await chrome.storage.local.set({ ttBrowserName: detected }); } catch (_) {}
  }
  return detected;
}

// Re-push EVERYTHING the extension already knows (cached Claude + Gemini
// readings and the ChatGPT estimate) to the desktop app. After we stopped
// auto-opening tabs, the readers only fire when you're on each site, so the
// desktop app could miss providers you haven't revisited. Calling this on
// startup and on the periodic alarm keeps the dashboard mirrored to whatever
// the extension currently has, without opening any tabs.
// Announce this browser to the desktop app so it appears in the tray's
// "Source browser" list immediately - even before it has any usage data.
async function registerBrowser() {
  await fetchTray("/register_browser", {
    browserId:   await browserId(),
    browserName: await browserName(),
  });
}

async function pushAllKnownReadings() {
  try {
    const { claudeRealUsage, geminiRealUsage } = await chrome.storage.local.get(
      ["claudeRealUsage", "geminiRealUsage"]);
    if (claudeRealUsage && claudeRealUsage.fiveHourPercent != null) {
      await fetchTray("/claude_usage", {
        browserId: await browserId(),
        browserName: await browserName(),
        fiveHourPercent: claudeRealUsage.fiveHourPercent,
        weeklyPercent:   claudeRealUsage.weeklyPercent,
        resetLabel:      claudeRealUsage.resetLabel,
      });
    }
    if (geminiRealUsage && geminiRealUsage.currentPercent != null) {
      await fetchTray("/gemini_usage", {
        browserId: await browserId(),
        browserName: await browserName(),
        currentPercent: geminiRealUsage.currentPercent,
        weeklyPercent:  geminiRealUsage.weeklyPercent,
        resetLabel:     geminiRealUsage.resetLabel,
        weeklyReset:    geminiRealUsage.weeklyReset,
        tier:           geminiRealUsage.tier,
      });
    }
  } catch (_) {}
  await pushChatgptEstimate();
}

async function pushChatgptEstimate() {
  const { limit, windowMs } = await chatgptConfig();
  const now = Date.now();
  const { chatgptMsgTimes } = await chrome.storage.local.get("chatgptMsgTimes");
  const times = (Array.isArray(chatgptMsgTimes) ? chatgptMsgTimes : [])
    .filter(t => now - t < windowMs);
  // Send THIS browser's raw timestamps tagged by id; the desktop app merges
  // across browsers. (If no desktop app is running the POST just no-ops.)
  await fetchTray("/chatgpt_usage", {
    browserId: await browserId(),
    browserName: await browserName(),
    timestamps: times,
    limit,
  });
}

async function updateBadge() {
  const { claudeRealUsage, geminiRealUsage, chatgptLimitHit } =
    await chrome.storage.local.get(["claudeRealUsage", "geminiRealUsage", "chatgptLimitHit"]);

  const claudePct = claudeRealUsage?.fiveHourPercent ?? null;
  const geminiPct = geminiRealUsage?.currentPercent ?? null;
  const chatgptHit = chatgptHitActive(chatgptLimitHit);

  // Expired hit — clear the stored flag and tell the tray app it lapsed.
  if (chatgptLimitHit?.hit && !chatgptHit) {
    await chrome.storage.local.remove("chatgptLimitHit");
    pushLimitHitToTray("chatgpt", false);
  }

  // Badge shows the highest known real percentage (Claude or Gemini),
  // then an actual ChatGPT limit-banner "!", then the ChatGPT *estimate*,
  // otherwise nothing.
  const knownPcts = [claudePct, geminiPct].filter(p => p !== null);
  const est = await chatgptEstimate();

  if (knownPcts.length > 0) {
    const topPct = Math.max(...knownPcts);
    chrome.action.setBadgeText({ text: `${Math.round(topPct)}` });
    const color = topPct >= 80 ? "#d22323" : topPct >= 60 ? "#e6a000" : "#00c878";
    chrome.action.setBadgeBackgroundColor({ color });
  } else if (chatgptHit) {
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#d22323" });
  } else if (est) {
    chrome.action.setBadgeText({ text: `${est.percent}` });
    const color = est.percent >= 80 ? "#d22323" : est.percent >= 60 ? "#e6a000" : "#00c878";
    chrome.action.setBadgeBackgroundColor({ color });
  } else {
    chrome.action.setBadgeText({ text: "" });
  }
}

// ── Claude usage page reader ────────────────────────────────────────────────
// Read Anthropic's own calculated percentage from claude.ai/settings/usage,
// rather than counting messages ourselves.

const CLAUDE_USAGE_URL = "https://claude.ai/settings/usage";
let _lastClaudeCheck = 0;
const CLAUDE_CHECK_INTERVAL_MS = 3 * 60 * 1000;   // every 3 minutes
let _pendingClaudeCleanup = null;

async function checkClaudeUsage() {
  const now = Date.now();
  if (now - _lastClaudeCheck < CLAUDE_CHECK_INTERVAL_MS) return;
  _lastClaudeCheck = now;

  try {
    const tab = await chrome.tabs.create({ url: CLAUDE_USAGE_URL, active: false });
    const cleanup = setTimeout(async () => {
      try { await chrome.tabs.remove(tab.id); } catch (_) {}
    }, 10000);   // 10s — allows time for SPA redirect (/new#settings/usage) + render
    _pendingClaudeCleanup = { tabId: tab.id, timer: cleanup };
  } catch (e) {
    // Not logged in, or claude.ai unreachable — fail silently
  }
}

async function handleClaudeUsageRead(data) {
  if (_pendingClaudeCleanup) {
    clearTimeout(_pendingClaudeCleanup.timer);
    try { await chrome.tabs.remove(_pendingClaudeCleanup.tabId); } catch (_) {}
    _pendingClaudeCleanup = null;
  }

  if (!data.foundAnyData) {
    await chrome.storage.local.set({
      claudeUsageReadFailed: true,
      claudeUsageDebugSample: data.rawTextSample,
    });
    return;
  }

  await chrome.storage.local.set({
    claudeRealUsage: {
      fiveHourPercent: data.fiveHourPercent,
      weeklyPercent:   data.weeklyPercent,
      resetLabel:      data.resetLabel,
      readAt:          Date.now(),
    },
    claudeUsageReadFailed: false,
  });

  await updateBadge();

  // Push to tray app (best-effort, auto-pairs / retries on 403).
  // Forward only the structured numbers, never rawTextSample / page text.
  await fetchTray("/claude_usage", {
    browserId:       await browserId(),
    browserName:     await browserName(),
    fiveHourPercent: data.fiveHourPercent,
    weeklyPercent:   data.weeklyPercent,
    resetLabel:      data.resetLabel,
  });
}

// ── Gemini usage page reader ─────────────────────────────────────────────────
// Same approach: read Google's own calculated percentage from
// gemini.google.com/usage, rather than counting messages ourselves.

const GEMINI_USAGE_URL = "https://gemini.google.com/usage";
let _lastGeminiCheck = 0;
const GEMINI_CHECK_INTERVAL_MS = 3 * 60 * 1000;   // every 3 minutes
let _pendingGeminiCleanup = null;

async function checkGeminiUsage() {
  const now = Date.now();
  if (now - _lastGeminiCheck < GEMINI_CHECK_INTERVAL_MS) return;
  _lastGeminiCheck = now;

  try {
    const tab = await chrome.tabs.create({ url: GEMINI_USAGE_URL, active: false });
    const cleanup = setTimeout(async () => {
      try { await chrome.tabs.remove(tab.id); } catch (_) {}
    }, 10000);
    _pendingGeminiCleanup = { tabId: tab.id, timer: cleanup };
  } catch (e) {
    // Not logged in, or gemini.google.com unreachable — fail silently
  }
}

async function handleGeminiUsageRead(data) {
  if (_pendingGeminiCleanup) {
    clearTimeout(_pendingGeminiCleanup.timer);
    try { await chrome.tabs.remove(_pendingGeminiCleanup.tabId); } catch (_) {}
    _pendingGeminiCleanup = null;
  }

  if (!data.foundAnyData) {
    await chrome.storage.local.set({
      geminiUsageReadFailed: true,
      geminiUsageDebugSample: data.rawTextSample,
    });
    return;
  }

  await chrome.storage.local.set({
    geminiRealUsage: {
      currentPercent: data.currentPercent,
      weeklyPercent:  data.weeklyPercent,
      resetLabel:     data.resetLabel,
      weeklyReset:    data.weeklyReset,
      tier:           data.tier,
      readAt:         Date.now(),
    },
    geminiUsageReadFailed: false,
  });

  await updateBadge();

  // Push to tray app (best-effort, auto-pairs / retries on 403).
  // Forward only the structured numbers, never rawTextSample / page text.
  await fetchTray("/gemini_usage", {
    browserId:      await browserId(),
    browserName:    await browserName(),
    currentPercent: data.currentPercent,
    weeklyPercent:  data.weeklyPercent,
    resetLabel:     data.resetLabel,
    weeklyReset:    data.weeklyReset,
    tier:           data.tier,
  });
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(async () => {
  await getIpcToken();
  await registerBrowser();
  await updateBadge();
  // No automatic usage-check tabs. We read Claude/Gemini passively whenever the
  // user is already on those sites (see usage_reader.js / gemini_usage_reader.js),
  // and only open a tab when the user explicitly clicks "Refresh" in the popup.
  // This alarm only refreshes the badge (e.g. expires a lapsed ChatGPT hit) and
  // never opens a tab.
  chrome.alarms.create("badge-refresh", { periodInMinutes: 5 });
  // Poll the tray for a "Refresh now" request (~30s). 0.5 min is the
  // smallest Chrome allows for alarms.
  chrome.alarms.create("tray-refresh-poll", { periodInMinutes: 0.5 });
  // Clean up any stale periodic alarms left by older versions.
  chrome.alarms.clear("claude-usage-check");
  chrome.alarms.clear("gemini-usage-check");
  checkTrayRefresh();   // don't wait for the first 30s alarm tick
});

chrome.runtime.onStartup.addListener(async () => {
  await getIpcToken();
  await registerBrowser();
  await updateBadge();
  await pushAllKnownReadings();
  checkTrayRefresh();
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "tray-refresh-poll") {
    await checkTrayRefresh();
  }
  if (alarm.name === "badge-refresh") {
    await updateBadge();
    await pushAllKnownReadings();   // keep the desktop dashboard mirrored
  }
});

// Immediate kick on EVERY service-worker wake (not just install/startup). MV3
// tears the worker down when idle; whatever re-spawns it (a message, an alarm,
// a fetch) also runs this top-level code. So the desktop app sees this browser
// register + receive its readings within a second of the worker waking, instead
// of waiting up to the 30s poll. Guarded so it runs at most once per wake.
let _wokeKick = false;
(async function kickOnWake() {
  if (_wokeKick) return;
  _wokeKick = true;
  try {
    await getIpcToken();
    await registerBrowser();
    await pushAllKnownReadings();
    await updateBadge();
  } catch (_) {}
})();

// ── Message listener ───────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "PUSH_ALL") {
    pushAllKnownReadings();   // popup opened -> mirror everything to the desktop app
    return false;
  }
  if (msg.type === "CLAUDE_USAGE_READ") {
    handleClaudeUsageRead(msg.data);
    return false;
  }
  if (msg.type === "FORCE_CLAUDE_CHECK") {
    _lastClaudeCheck = 0;
    checkClaudeUsage();
    return false;
  }
  if (msg.type === "GEMINI_USAGE_READ") {
    handleGeminiUsageRead(msg.data);
    return false;
  }
  if (msg.type === "FORCE_GEMINI_CHECK") {
    _lastGeminiCheck = 0;
    checkGeminiUsage();
    return false;
  }
  if (msg.type === "LIMIT_HIT_UPDATED") {
    updateBadge();
    if (msg.provider) pushLimitHitToTray(msg.provider, true);
    return false;
  }
  if (msg.type === "CHATGPT_MESSAGE_SENT") {
    recordChatgptMessage(msg.at);
    return false;
  }
});

async function pushLimitHitToTray(provider, hit) {
  await fetchTray("/limit_hit", { provider, hit });
}

// React to real-usage / limit-hit flags written directly by content.js
chrome.storage.onChanged.addListener((changes) => {
  if (changes.chatgptLimitHit || changes.claudeRealUsage || changes.geminiRealUsage) {
    updateBadge();
  }
});
