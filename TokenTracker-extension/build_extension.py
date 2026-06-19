#!/usr/bin/env python3
"""
build_extension.py — package the TokenTracker browser extension per target.

Chrome (and Brave/Edge) reject `background.scripts` under Manifest V3, and
Firefox uses `background.scripts` (not `service_worker`) plus a gecko id. A
single manifest can't satisfy both, so we ship two:

    manifest.json          -> Chromium (service_worker)   [loads directly]
    manifest.firefox.json  -> Firefox  (background.scripts + gecko)

The Chromium zip uses manifest.json as-is. The Firefox zip swaps the Firefox
manifest in AS manifest.json (every other file is identical).

    dist/tokentracker-chromium.zip   <- Chrome / Brave / Edge
    dist/tokentracker-firefox.zip    <- Firefox

Usage:  python build_extension.py
"""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")

# Shared payload (manifest handled separately per target).
SHARED = ["popup.html", "src", "icons", "README.md"]

EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", "__pycache__", ".git"}
EXCLUDE_EXT = {".pyc", ".map"}


def _iter_files(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES]
        for f in files:
            if f in EXCLUDE_NAMES or os.path.splitext(f)[1] in EXCLUDE_EXT:
                continue
            yield os.path.join(base, f)


def _add(zf, path, arcname=None):
    zf.write(path, arcname or os.path.relpath(path, HERE))


def build(zip_name, manifest_src):
    """manifest_src is the file written into the zip AS 'manifest.json'."""
    out = os.path.join(DIST, zip_name)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        _add(zf, os.path.join(HERE, manifest_src), "manifest.json")
        for item in SHARED:
            p = os.path.join(HERE, item)
            if not os.path.exists(p):
                continue
            if os.path.isdir(p):
                for f in _iter_files(p):
                    _add(zf, f)
            else:
                _add(zf, p)
    print(f"  built {zip_name}  ({os.path.getsize(out):,} bytes)  [manifest: {manifest_src}]")
    return out


def main():
    os.makedirs(DIST, exist_ok=True)
    print("Packaging TokenTracker extension (per-browser manifests)...")
    build("tokentracker-chromium.zip", "manifest.json")
    build("tokentracker-firefox.zip", "manifest.firefox.json")
    print("Done. Load instructions:")
    print("  Chrome/Brave/Edge: chrome://extensions -> Developer mode -> Load unpacked (this folder)")
    print("  Firefox: unzip tokentracker-firefox.zip, then about:debugging ->")
    print("           This Firefox -> Load Temporary Add-on -> pick its manifest.json")


if __name__ == "__main__":
    main()
