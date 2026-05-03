#!/usr/bin/env python3
"""
Local Windows build helper for Golf Shot Analyzer.

Usage:
    python build.py                        # build to dist/square-lm-coach.exe
    python build.py --clean                # remove build/ and dist/ first
    python build.py --version v1.0.0       # rename output with version tag

Requires: pip install pyinstaller requests
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser(description="Build square-lm-coach.exe")
    ap.add_argument("--clean",   action="store_true", help="Remove build/ and dist/ before building")
    ap.add_argument("--version", default=None,        help="Version tag to embed in output filename (e.g. v1.0.0)")
    args = ap.parse_args()

    if args.clean:
        for d in ("build", "dist"):
            p = ROOT / d
            if p.exists():
                shutil.rmtree(p)
                print(f"Removed {p}")

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "golf_shot_analyzer.spec"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        sys.exit(result.returncode)

    src = ROOT / "dist" / "square-lm-coach.exe"
    if not src.exists():
        print("ERROR: build succeeded but square-lm-coach.exe not found in dist/")
        sys.exit(1)

    if args.version:
        dst = ROOT / "dist" / f"square-lm-coach-{args.version}-windows.exe"
        src.rename(dst)
        print(f"\nBuilt: {dst}")
    else:
        print(f"\nBuilt: {src}")
        print("Tip: pass --version v1.0.0 to embed a version tag in the filename.")


if __name__ == "__main__":
    main()
