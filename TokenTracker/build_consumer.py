"""
Build TokenTracker (Consumer Edition) .exe
Run: uv run python build_consumer.py
Output: dist/TokenTracker.exe (+ dist/TokenTracker.exe.sha256)
"""

import hashlib, subprocess, sys, shutil
from pathlib import Path

ROOT     = Path(__file__).parent
DIST_DIR = ROOT / "dist"
BUILD_DIR= ROOT / "build"
EXE_NAME = "TokenTracker.exe"


def clean():
    for d in (DIST_DIR, BUILD_DIR):
        if d.exists():
            shutil.rmtree(d)
            print(f"  cleaned {d}")


def _write_checksum(exe: Path) -> str:
    """Compute the SHA-256 of the built exe and write a sidecar .sha256 file
    in the standard '<hash>  <filename>' format (works with `sha256sum -c`)."""
    h = hashlib.sha256()
    with open(exe, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    (exe.parent / f"{exe.name}.sha256").write_text(f"{digest}  {exe.name}\n")
    return digest


def build():
    print("Building TokenTracker (Consumer Edition)...\n")
    clean()

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "TokenTracker-consumer.spec", "--clean"],
        cwd=ROOT,
    )

    if result.returncode != 0:
        print("\nBuild failed.")
        sys.exit(1)

    exe = DIST_DIR / EXE_NAME
    if exe.exists():
        size_mb = exe.stat().st_size / 1_048_576
        digest = _write_checksum(exe)
        print("\nBuild complete!")
        print(f"    {exe}")
        print(f"    Size: {size_mb:.1f} MB")
        print(f"    SHA-256: {digest}")
        print(f"    Checksum written to {exe.name}.sha256")
        print("\n    Publish the .sha256 file alongside the .exe on the release")
        print("    page so users can verify their download (see SECURITY.md).")
        print("\nNext: run installer/ISCC.exe TokenTracker.iss to build the installer")
    else:
        print(f"\nExpected {exe} - not found.")
        sys.exit(1)


if __name__ == "__main__":
    build()
