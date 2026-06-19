// content.js — auto-detection only, no manual settings
//
// Claude:  detects when a message is sent → triggers an immediate real
//          usage check (claude.ai/settings/usage) instead of waiting 3 min
// ChatGPT: no usage page exists — detect the "limit reached" banner text,
//          the only real signal OpenAI gives us
//
// Copilot was deliberately dropped: company/M365 accounts expose no
// personal usage signal at all (not even a limit-reached banner in most
// tiers), so any detection here would be fabricated rather than real.

(function () {
  "use strict";

  const host = window.location.hostname;

  // The extension can be reloaded/updated while this content script is still
  // running in an open tab, which "invalidates" the context: chrome.runtime
  // becomes unusable and any chrome.* call throws "Extension context
  // invalidated". Guard EVERY chrome.* call through these helpers so a stale
  // content script fails silently instead of spamming the page console.
  function ctxAlive() {
    try { return !!(chrome.runtime && chrome.runtime.id); } catch (_) { return false; }
  }
  function safeSend(msg) {
    if (!ctxAlive()) return;
    try { chrome.runtime.sendMessage(msg); } catch (_) {}
  }
  function safeStore(obj) {
    if (!ctxAlive()) return;
    try { chrome.storage.local.set(obj); } catch (_) {}
  }

  // Claude usage is read passively by usage_reader.js whenever you're on
  // claude.ai/settings/usage, and on demand via the popup's Refresh button.
  // We deliberately do NOT open a background tab when you send a message -
  // the app never opens tabs on its own.

  // ── ChatGPT: detect "limit reached" banner (no usage page exists) ─────────
  // OpenAI exposes no percentage anywhere. The only real signal is this
  // banner appearing, which means usage = 100% for the current window.
  const CHATGPT_HOSTS = ["chat.openai.com", "chatgpt.com"];

  if (CHATGPT_HOSTS.indexOf(host) !== -1) {
    // Estimated usage: OpenAI exposes no percentage, so we count the
    // messages YOU send and let background.js compute a rolling-window
    // estimate. This is clearly labelled "Estimated" in the UI - it is not
    // an official quota reading. Detecting an outgoing message: ChatGPT adds
    // a user-turn element to the transcript when you send.
    (function trackChatgptMessages() {
      // ChatGPT's DOM changes often, so try several selectors for a user turn
      // and use whichever finds the most elements. Counting NEW user-turn
      // elements (tracked in a WeakSet) is resilient to re-renders: a node
      // already seen is never re-counted, and a transient re-render that
      // recreates nodes counts each real new turn at most once.
      const SELECTORS = [
        '[data-message-author-role="user"]',
        'div[data-message-author-role="user"]',
        '[data-testid^="conversation-turn"] [data-message-author-role="user"]',
        'article [data-message-author-role="user"]',
      ];
      function userTurns() {
        let best = [];
        for (const sel of SELECTORS) {
          let els;
          try { els = document.querySelectorAll(sel); } catch (_) { continue; }
          if (els && els.length > best.length) best = Array.from(els);
        }
        return best;
      }
      const seen = new WeakSet();
      userTurns().forEach(el => seen.add(el));   // don't count history on load
      let _debounce = null;
      let _obs = null;
      _obs = new MutationObserver(() => {
        // If the extension was reloaded, this stale script must stop touching
        // chrome.* - disconnect and go quiet.
        if (!ctxAlive()) { try { _obs.disconnect(); } catch (_) {} return; }
        // Debounce: a single send can trigger many mutations as the turn renders.
        if (_debounce) return;
        _debounce = setTimeout(() => {
          _debounce = null;
          userTurns().forEach(el => {
            if (!seen.has(el)) {
              seen.add(el);
              safeSend({ type: "CHATGPT_MESSAGE_SENT", at: Date.now() });
            }
          });
        }, 250);
      });
      _obs.observe(document.body, { childList: true, subtree: true });
    })();

    // Patterns must be specific to OpenAI's actual limit banner. Earlier,
    // broad patterns like /try again later/i and /switching to.*mini/i
    // matched ordinary conversation text (a chat that merely *mentions*
    // limits, or any message containing "mini"), which — combined with the
    // formerly-sticky hit — pinned ChatGPT at 100% forever. We now require
    // the distinctive limit phrasing AND only scan likely banner elements.
    const CHATGPT_LIMIT_PATTERNS = [
      /you'?ve reached (our|your|the) [\w\s.-]{0,20}?limit/i,
      /you'?ve hit your [\w\s.-]{0,20}?limit/i,
      /reached the [\w\s.-]{0,20}?limit for (gpt|messages|your)/i,
    ];

    // Limit notices render in small banner/toast containers, never in the
    // message transcript. Scanning these instead of document.body.innerText
    // avoids matching the content of the conversation itself.
    function candidateBannerText() {
      const sel = [
        '[role="alert"]',
        '[role="status"]',
        '[data-testid*="limit" i]',
        '[class*="notice" i]',
        '[class*="banner" i]',
        '[class*="toast" i]',
      ].join(",");
      const parts = [];
      document.querySelectorAll(sel).forEach(el => {
        const t = (el.innerText || "").trim();
        // Banners are short; skip anything transcript-sized to be safe.
        if (t && t.length < 400) parts.push(t);
      });
      return parts.join("\n");
    }

    let _chatgptReported = false;
    function checkChatgptLimitText() {
      const text = candidateBannerText();
      const hit = CHATGPT_LIMIT_PATTERNS.some(p => p.test(text));

      if (hit) {
        safeStore({ chatgptLimitHit: { hit: true, detectedAt: Date.now() } });
        if (!_chatgptReported) {
          _chatgptReported = true;
          safeSend({ type: "LIMIT_HIT_UPDATED", provider: "chatgpt" });
        }
      } else if (_chatgptReported) {
        // Banner cleared (new rolling window) — re-arm so a future hit
        // reports again. background.js handles TTL expiry independently.
        _chatgptReported = false;
      }
    }

    const _limitTimer = setInterval(() => {
      if (!ctxAlive()) { clearInterval(_limitTimer); return; }
      checkChatgptLimitText();
    }, 5000);
    checkChatgptLimitText();
  }

})();
