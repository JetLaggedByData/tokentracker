// usage_reader.js — reads Claude's own usage numbers from claude.ai/settings/usage
//
// This is the "overall, accurate" source: Anthropic's server already knows
// your usage across phone, browser, desktop app — everything. We just read
// the number they show you, instead of counting messages ourselves.
//
// Runs on claude.ai broadly (Claude.ai is a single-page app, so the usage
// page can appear as a path /settings/usage OR a hash route like
// /new#settings/usage depending on how navigation happened). We check both,
// and re-check on hash changes since SPA navigation doesn't reload the script.

(function () {
  "use strict";

  // ── Guard: extension context can be invalidated mid-navigation ────────────
  // (e.g. tab closed by background.js, or extension reloaded). Any chrome.*
  // call after that throws "Cannot read properties of undefined". Wrap every
  // call site so a stray timer never crashes the page.
  function isExtensionContextValid() {
    return typeof chrome !== "undefined" &&
           !!chrome.runtime &&
           !!chrome.runtime.id;   // throws/undefined once context is gone
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
    return path.includes("/settings/usage") || path.includes("/settings/limits") ||
           hash.includes("settings/usage")  || hash.includes("settings/limits");
  }

  // ── Selector strategy ────────────────────────────────────────────────────
  function extractPercentText(text) {
    const match = text.match(/(\d{1,3})\s*%/);
    return match ? parseInt(match[1]) : null;
  }

  function findUsageBars() {
    const results = {};

    // Confirmed real structure (June 2026) — two-level hierarchy:
    //
    // <section><h3>Current session</h3> ... <progressbar aria-valuenow="13">  → 5-hour
    // <section><h3>Weekly limits</h3>
    //    <row><span>All models</span>   <progressbar aria-valuenow="15">     → weekly (all models)
    //    <row><span>Sonnet only</span>   <progressbar aria-valuenow="3">      → weekly (Sonnet only, narrower)
    //
    // The aria-label on the progressbar itself is just generic "Usage" —
    // the real label lives in the <h3> section heading and the row's own
    // label span, not on the progressbar element.

    document.querySelectorAll('[role="progressbar"]').forEach(el => {
      const aria = el.getAttribute("aria-valuenow");
      if (aria === null) return;
      const pct = parseInt(aria);

      // Section-level heading (e.g. "Current session" or "Weekly limits")
      const section = el.closest("section");
      const heading = section ? section.querySelector("h3") : null;
      const sectionLabel = (heading ? heading.textContent : "").toLowerCase();

      // Row-level label (e.g. "All models", "Sonnet only", "Current session")
      // NOTE: the progressbar element itself has classes "flex h-2 w-full
      // items-center ..." which ALSO matches "div.flex.w-full" — so closest()
      // matches itself immediately instead of walking up. Start the search
      // from the parent instead.
      const row = el.parentElement
        ? (el.parentElement.closest("div.flex.w-full") || el.parentElement.parentElement)
        : null;
      const rowLabelEl = row ? row.querySelector(".text-primary") : null;
      const rowLabel = (rowLabelEl ? rowLabelEl.textContent : "").toLowerCase();

      const combined = sectionLabel + " " + rowLabel;

      if (combined.includes("session") || combined.includes("5 hour") || combined.includes("5-hour")) {
        if (results.fiveHour === undefined) results.fiveHour = pct;
      } else if (combined.includes("week")) {
        // Prefer "all models" as the headline weekly number — it's the
        // broader cap that determines when you're actually locked out.
        if (rowLabel.includes("all models") || results.weekly === undefined) {
          results.weekly = pct;
        }
        // Stash narrower sub-limits separately, not shown in the main pill
        // but available if useful later.
        if (rowLabel.includes("sonnet")) results.weeklySonnet = pct;
      } else if (results.fiveHour === undefined) {
        // Unattributed first bar — assume session (listed first on the page)
        results.fiveHour = pct;
      } else if (results.weekly === undefined) {
        results.weekly = pct;
      }
    });

    // Fallback: text-pattern matching if no progressbar elements found at all
    if (Object.keys(results).length === 0) {
      const allText = document.body.innerText || "";
      const lines = allText.split("\n").map(l => l.trim()).filter(Boolean);

      lines.forEach((line, i) => {
        const lower = line.toLowerCase();
        const pct = extractPercentText(line);
        if (pct === null) return;

        const context = ((lines[i-1] || "") + " " + (lines[i+1] || "")).toLowerCase();
        if (lower.includes("current session") || context.includes("current session") ||
            lower.includes("5-hour") || lower.includes("5 hour") || context.includes("session")) {
          results.fiveHour = pct;
        } else if (lower.includes("week") || context.includes("week")) {
          results.weekly = pct;
        }
      });
    }

    return results;
  }

  // Match a reset value that is EITHER a clock time (4:37 PM) or a duration
  // (4 hr 36 min / 2h 10m / 45 min / 3 days). Anchored pieces only, so we never
  // swallow trailing text like an adjacent "8% used" label.
  const _CLOCK = "\\d{1,2}:\\d{2}\\s*(?:[ap]\\.?m\\.?)?";
  const _DUR   = "(?:\\d+\\s*(?:hours?|hrs?|h|minutes?|mins?|m|days?|d|weeks?|w)\\s*){1,3}";
  const _RESET_RE = new RegExp(
    "resets?\\s+(?:in|at)\\s+(" + _CLOCK + "|" + _DUR + ")", "i");

  function _cleanReset(match) {
    return match ? match[1].replace(/\\s+/g, " ").trim() : null;
  }

  function findResetTime() {
    // Multiple "Resets..." labels exist on the page (session + weekly rows).
    // Find the one specifically tied to the Current Session row.
    const sessionHeading = Array.from(document.querySelectorAll("h3, span"))
      .find(el => /current session/i.test(el.textContent || ""));

    if (sessionHeading) {
      const row = sessionHeading.closest("div.flex.w-full") ||
                  sessionHeading.closest("section") ||
                  sessionHeading.parentElement?.parentElement;
      if (row) {
        const r = _cleanReset((row.textContent || "").match(_RESET_RE));
        if (r) return r;
      }
    }

    // Fallback: first "Resets..." value found anywhere on the page
    return _cleanReset((document.body.innerText || "").match(_RESET_RE));
  }

  function report() {
    if (!isExtensionContextValid()) return;

    const bars  = findUsageBars();
    const reset = findResetTime();

    safeSendMessage({
      type: "CLAUDE_USAGE_READ",
      data: {
        fiveHourPercent: bars.fiveHour ?? null,
        weeklyPercent:   bars.weekly   ?? null,
        resetLabel:      reset,
        foundAnyData:    Object.keys(bars).length > 0,
        rawTextSample:   (document.body.innerText || "").slice(0, 500),
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

  // Initial check on script injection
  scheduleReport();

  // Claude.ai is an SPA — re-check if the hash/path changes without a full
  // page reload (e.g. user clicks Settings -> Usage from within the app)
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
