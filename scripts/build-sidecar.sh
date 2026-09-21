#!/usr/bin/env bash
#
# Build the Python backend as a Tauri sidecar binary for the current platform.
#
# Output: src-tauri/binaries/munger-backend-<target-triple>
# The Tauri shell (src-tauri/src/main.rs) spawns this binary; it must be
# present before `tauri dev` / `tauri build`.
#
# Requirements: python3, pip, and the project's .venv (or set PY=python3).
# On Windows, run the equivalent PyInstaller command manually (--add-data
# uses ";" as separator there).
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS-$ARCH" in
  Darwin-arm64)   TRIPLE="aarch64-apple-darwin" ;;
  Darwin-x86_64)  TRIPLE="x86_64-apple-darwin" ;;
  Linux-x86_64)   TRIPLE="x86_64-unknown-linux-gnu" ;;
  Linux-aarch64)  TRIPLE="aarch64-unknown-linux-gnu" ;;
  *) echo "unsupported platform: $OS-$ARCH" >&2; exit 1 ;;
esac

PY="${PY:-.venv/bin/python}"
"$PY" -m pip install -q pyinstaller

OUT="src-tauri/binaries"
mkdir -p "$OUT"
"$PY" -m PyInstaller \
  --noconfirm --onefile \
  --name "munger-backend-$TRIPLE" \
  --distpath "$OUT" \
  --add-data "static:static" \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  sidecar.py

echo "built $OUT/munger-backend-$TRIPLE"
echo "next: npm run tauri dev   (or: tauri dev)"
