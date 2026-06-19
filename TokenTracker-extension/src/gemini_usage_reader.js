// gemini_usage_reader.js — reads Gemini's own usage numbers from
// gemini.google.com/usage
//
// Same approach as Claude: Google's own server already calculates this
// number across every device. We just read what the page already shows,
// instead of guessing from message counts.
//
// Runs only on gemini.google.com, and only does anything once the
// /usage page (or its hash-route equivalent) is actually open.

(function () {
  "use strict";

  function isExtensionContextValid() {
    return typeof chrome !== "undefined" &&
           !!chrome.runtime &&
           !!chrome.runtime.id;
  }

  function safeSendMessage(payload) {
    if (!isExtensionContextValid()) return;
    try {
      chrome.runtime.sendMessage(payload);
    } catch (e) {
      // Context invalidated between the check and the call — ignore.
    }
  }

  function isUsagePage() {
    const path = window.location.pathname || "";
    const hash = window.location.hash || "";
    return path.includes("/usage") || hash.includes("usage");
  }

  // ── Selector strategy ────────────────────────────────────────────────────
  // Confirmed real structure (June 2026):
  //
  // <div data-test-id="gxu-currently">      ← "Current usage" (5-hour-style)
  //   <p>0% used</p>
  //   <p class="reset-time-luminous">Resets at 11:37 AM</p>
  // </div>
  // <div data-test-id="gxu-weekly">         ← "Weekly limit"
  //   <p class="reset-time-luminous">Resets Jun 23 at 11:37 AM</p>
  //   <p>1% used</p>
  // </div>
  //
  // The percentage lives in plain text ("X% used"), not in aria-valuenow —
  // unlike Claude, so we regex-extract it rather than reading an attribute.

  function extractPercent(text) {
    const match = (text || "").match(/(\d{1,3})\s*%\s*used/i);
    return match ? parseInt(match[1]) : null;
  }

  function findGeminiUsage() {
    const result = {};

    const currentEl = document.querySelector('[data-test-id="gxu-currently"]');
    if (currentEl) {
      result.currentPercent = extractPercent(currentEl.textContent);
      const resetEl = currentEl.querySelector(".reset-time-luminous");
      result.currentReset = resetEl ? resetEl.textContent.trim() : null;
    }

    const weeklyEl = document.querySelector('[data-test-id="gxu-weekly"]');
    if (weeklyEl) {
      result.weeklyPercent = extractPercent(weeklyEl.textContent);
      const resetEl = weeklyEl.querySelector(".reset-time-luminous");
      result.weeklyReset = resetEl ? resetEl.textContent.trim() : null;
    }

    const tierEl = document.querySelector(".tier-pill");
    result.tier = tierEl ? tierEl.textContent.trim() : null;

    return result;
  }

  function report() {
    if (!isExtensionContextValid()) return;

    const usage = findGeminiUsage();
    const foundAnyData = usage.currentPercent !== undefined ||
                          usage.weeklyPercent !== undefined;

    safeSendMessage({
      type: "GEMINI_USAGE_READ",
      data: {
        currentPercent: usage.currentPercent ?? null,
        weeklyPercent:  usage.weeklyPercent  ?? null,
        resetLabel:     usage.currentReset   ?? null,
        weeklyReset:    usage.weeklyReset    ?? null,
        tier:           usage.tier           ?? null,
        foundAnyData,
        rawTextSample: (document.body.innerText || "").slice(0, 500),
      }
    });
  }

  function scheduleReport() {
    if (!isUsagePage()) return;
    if (document.readyState === "complete") {
      setTimeout(report, 1500);
    } else {
      window.addEventListener("load", () => setTimeout(report, 1500));
    }
  }

  scheduleReport();

  // Gemini is an SPA too — re-check on hash/path changes without reload
  window.addEventListener("hashchange", scheduleReport);

  let _lastPath = window.location.pathname + window.location.hash;
  setInterval(() => {
    const current = window.location.pathname + window.location.hash;
    if (current !== _lastPath) {
      _lastPath = current;
      scheduleReport();
    }
  }, 1000);

})();
