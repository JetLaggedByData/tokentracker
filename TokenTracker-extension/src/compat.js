// compat.js — cross-browser shim (Chrome/Brave/Edge + Firefox)
//
// Chromium browsers expose the extension API as `chrome.*`. Firefox exposes it
// as `browser.*` and ALSO provides a `chrome.*` alias for callback-style calls.
// The rest of this extension is written against `chrome.*`, which works on all
// four targets. This shim just guarantees the global exists and points the two
// names at each other so either can be used without ReferenceError.
(function () {
  "use strict";
  // Firefox: `browser` exists. If `chrome` is somehow missing, alias it.
  if (typeof globalThis.browser !== "undefined" && typeof globalThis.chrome === "undefined") {
    globalThis.chrome = globalThis.browser;
  }
  // Chromium: `chrome` exists. Provide a `browser` alias for any code/library
  // that prefers it (harmless on Firefox where it already exists).
  if (typeof globalThis.chrome !== "undefined" && typeof globalThis.browser === "undefined") {
    globalThis.browser = globalThis.chrome;
  }
})();
