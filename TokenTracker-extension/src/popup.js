// popup.js — pure auto-detection display, no manual settings
//
// Claude: real percentage read from claude.ai/settings/usage (accurate)
// Gemini: real percentage read from gemini.google.com/usage (accurate)
// ChatGPT: no usage page exists, so we detect the "you've hit your limit"
// banner text directly. Until that fires we show "—" (unknown) rather
// than guessing a fake percentage.
//
// Copilot was deliberately dropped: company/M365 accounts expose no
// personal usage signal at all — not even a limit-reached banner in most
// tiers — so there is nothing real to detect or display.

const PROVIDER_META = {
  claude:  { name: "Claude",  color: "#CC785C" },
  gemini:  { name: "Gemini",  color: "#4285F4" },
  chatgpt: { name: "ChatGPT", color: "#10A37F" },
};

function esc(s) {
  return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function fillClass(pct) {
  if (pct >= 80) return "fill-red";
  if (pct >= 60) return "fill-amber";
  return "fill-green";
}

function pillClass(pct) {
  if (pct === null) return "pill-unknown";
  if (pct >= 80) return "pill-red";
  if (pct >= 60) return "pill-amber";
  return "pill-green";
}

function timeAgo(timestamp) {
  if (!timestamp) return null;
  const secs = Math.floor((Date.now() - timestamp) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ago`;
}

function stripResetPrefix(label) {
  if (!label) return null;
  const lower = label.toLowerCase();
  for (const prefix of ["resets in ", "resets ", "in "]) {
    if (lower.startsWith(prefix)) return label.slice(prefix.length);
  }
  return label;
}

// ── Build one provider card ──────────────────────────────────────────────────

// Tiny DOM helper: make an element with a class + optional text. Building the
// card with real nodes (instead of innerHTML) removes the AMO "unsafe innerHTML"
// warning and is inherently XSS-proof - no values are ever parsed as markup.
function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function buildCard(key, info) {
  const meta = PROVIDER_META[key];
  const { pct, countdown, source, ago, estimated } = info;

  const estTag = estimated ? "~" : "";   // leading ~ marks an estimate
  const pillText = pct === null ? "—" : `${estTag}${Math.round(pct)}% • ${countdown}`;
  const fillPct  = pct === null ? 0 : pct;
  const pctText  = pct === null ? "—" : estTag + Math.round(pct) + "%";

  const card = el("div", "pcard");

  // Top row: dot + name (+ badge) on the left, usage pill on the right.
  const top = el("div", "pcard-top");
  const nameWrap = el("div", "pcard-name");
  const dot = el("div", "pdot");
  dot.style.background = meta.color;          // a colour constant, not user data
  nameWrap.appendChild(dot);
  nameWrap.appendChild(document.createTextNode(meta.name));
  if (estimated) {
    nameWrap.appendChild(el("span", "sync-badge badge-est", "estimated"));
  } else if (pct !== null) {
    nameWrap.appendChild(el("span", "sync-badge badge-synced", "synced"));
  }
  top.appendChild(nameWrap);
  top.appendChild(el("div", "usage-pill " + pillClass(pct), pillText));
  card.appendChild(top);

  // Battery: body with a width-scaled fill, a nub, and the % label.
  const wrap = el("div", "bat-wrap");
  const body = el("div", "bat-body");
  const fill = el("div", "bat-fill " + fillClass(fillPct));
  fill.style.width = fillPct + "%";
  body.appendChild(fill);
  wrap.appendChild(body);
  wrap.appendChild(el("div", "bat-nub"));
  wrap.appendChild(el("div", "bat-pct", pctText));
  card.appendChild(wrap);

  // Source line.
  const srcText = source + (ago ? ` · checked ${ago}` : "");
  card.appendChild(el("div", "source-tag", srcText));

  return card;
}

// ── Gather data for each provider from auto-detected sources only ──────────────

async function gatherProviderData() {
  const stored = await chrome.storage.local.get([
    "claudeRealUsage",
    "geminiRealUsage",
    "chatgptLimitHit",
    "chatgptMsgTimes",
    "chatgptLimit",
    "chatgptWindowMs",
  ]);

  const result = {};

  // Claude — real percentage from claude.ai/settings/usage
  const cr = stored.claudeRealUsage;
  if (cr && cr.fiveHourPercent !== null && cr.fiveHourPercent !== undefined) {
    result.claude = {
      pct:       cr.fiveHourPercent,
      countdown: stripResetPrefix(cr.resetLabel) || "≤5h",
      source:    "Read from claude.ai/settings/usage",
      ago:       timeAgo(cr.readAt),
    };
  } else {
    result.claude = {
      pct: null, countdown: "≤5h",
      source: "Visit claude.ai to sync — no data yet", ago: null,
    };
  }

  // Gemini — real percentage from gemini.google.com/usage
  const gr = stored.geminiRealUsage;
  if (gr && gr.currentPercent !== null && gr.currentPercent !== undefined) {
    result.gemini = {
      pct:       gr.currentPercent,
      countdown: stripResetPrefix(gr.resetLabel) || "≤5h",
      source:    gr.tier ? `${gr.tier} — gemini.google.com/usage` : "Read from gemini.google.com/usage",
      ago:       timeAgo(gr.readAt),
    };
  } else {
    result.gemini = {
      pct: null, countdown: "≤5h",
      source: "Visit gemini.google.com to sync — no data yet", ago: null,
    };
  }

  // ChatGPT — only know "hit limit" (100%) or unknown. No partial number
  // exists. The hit is valid only within the ~3h rolling window; an older
  // detection has lapsed and should read as unknown, not a stale 100%.
  const CHATGPT_HIT_TTL_MS = 3 * 60 * 60 * 1000;
  const CHATGPT_WINDOW_MS  = (Number.isFinite(stored.chatgptWindowMs) && stored.chatgptWindowMs > 0)
    ? stored.chatgptWindowMs : CHATGPT_HIT_TTL_MS;
  const CHATGPT_LIMIT = (Number.isFinite(stored.chatgptLimit) && stored.chatgptLimit > 0)
    ? stored.chatgptLimit : 160;
  const cg = stored.chatgptLimitHit;
  const cgActive = cg && cg.hit && (Date.now() - (cg.detectedAt || 0)) < CHATGPT_HIT_TTL_MS;
  if (cgActive) {
    // A real limit banner fired - this is the one true signal OpenAI gives.
    result.chatgpt = {
      pct: 100, countdown: "≤3h",
      source: "Limit reached on chatgpt.com",
      ago: timeAgo(cg.detectedAt),
    };
  } else {
    // No official %. Estimate from messages you sent in the rolling window.
    const now = Date.now();
    const times = Array.isArray(stored.chatgptMsgTimes) ? stored.chatgptMsgTimes : [];
    const used = times.filter(t => now - t < CHATGPT_WINDOW_MS).length;
    if (used > 0) {
      const pct = Math.min(100, Math.round(used / CHATGPT_LIMIT * 100));
      result.chatgpt = {
        pct, countdown: "≤3h",
        source: `Estimated · ${used}/${CHATGPT_LIMIT} msgs (OpenAI shows no official %)`,
        ago: null, estimated: true,
      };
    } else {
      result.chatgpt = {
        pct: null, countdown: "≤3h",
        source: "No messages yet this window · OpenAI shows no usage %",
        ago: null,
      };
    }
  }

  return result;
}

async function render() {
  const list = document.getElementById("providerList");
  while (list.firstChild) list.removeChild(list.firstChild);   // clear (no innerHTML)

  const data = await gatherProviderData();

  // Order: whichever is closest to its limit shown first
  const order = Object.keys(PROVIDER_META).sort((a, b) => {
    const pa = data[a].pct ?? -1;
    const pb = data[b].pct ?? -1;
    return pb - pa;
  });

  order.forEach(key => list.appendChild(buildCard(key, data[key])));
}

// ── Init ──────────────────────────────────────────────────────────────────────

(async function () {
  document.getElementById("headerDate").textContent =
    new Date().toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

  await render();

  // Mirror current readings to the desktop app whenever the popup is opened.
  try { chrome.runtime.sendMessage({ type: "PUSH_ALL" }); } catch (_) {}

  document.getElementById("checkNowBtn").addEventListener("click", async () => {
    const btn = document.getElementById("checkNowBtn");
    btn.textContent = "Checking…";
    btn.disabled = true;
    chrome.runtime.sendMessage({ type: "FORCE_CLAUDE_CHECK" });
    setTimeout(async () => {
      await render();
      btn.textContent = "Refresh Claude usage";
      btn.disabled = false;
    }, 4000);
  });

  document.getElementById("checkGeminiBtn").addEventListener("click", async () => {
    const btn = document.getElementById("checkGeminiBtn");
    btn.textContent = "Checking…";
    btn.disabled = true;
    chrome.runtime.sendMessage({ type: "FORCE_GEMINI_CHECK" });
    setTimeout(async () => {
      await render();
      btn.textContent = "Refresh Gemini usage";
      btn.disabled = false;
    }, 4000);
  });
})();
