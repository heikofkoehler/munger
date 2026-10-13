#!/usr/bin/env bash
#
# package.sh — End-to-end packaging script for Munger desktop installers.
#
# Usage:
#   ./scripts/package.sh               # Full build: sidecar + icons + installer
#   ./scripts/package.sh --skip-sidecar # Fast rebuild: skip PyInstaller if sidecar is fresh
#
# Outputs:
#   dist/Munger_<version>_<arch>.dmg  (macOS disk image installer)
#   dist/Munger.app                   (macOS application bundle)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Ensure common tool locations are on PATH
export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

SKIP_SIDECAR=false
for arg in "$@"; do
  case "$arg" in
    --skip-sidecar) SKIP_SIDECAR=true ;;
    -h|--help)
      echo "Usage: ./scripts/package.sh [--skip-sidecar]"
      exit 0
      ;;
  esac
done

echo "=================================================="
echo " Packaging Munger Desktop Installer"
echo "=================================================="

# ── 1. Check prerequisites ───────────────────────────────────
echo "--> Checking build prerequisites..."

if ! command -v cargo &>/dev/null; then
  echo "ERROR: 'cargo' not found. Please install Rust: https://rustup.rs" >&2
  exit 1
fi

if ! command -v npx &>/dev/null; then
  echo "ERROR: 'npx' not found. Please install Node.js: https://nodejs.org" >&2
  exit 1
fi

PY="${PY:-.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  if command -v python3 &>/dev/null; then
    PY="python3"
  else
    echo "ERROR: Python interpreter not found (looked for .venv/bin/python and python3)" >&2
    exit 1
  fi
fi

# ── 2. Build Python backend sidecar ──────────────────────────
if [ "$SKIP_SIDECAR" = false ]; then
  echo "--> Building Python backend sidecar (PyInstaller)..."
  bash "$ROOT/scripts/build-sidecar.sh"
else
  echo "--> Skipping sidecar build (--skip-sidecar requested)."
fi

# ── 3. Check and generate platform icons ────────────────────
if [[ ! -f "$ROOT/src-tauri/icons/icon.icns" ]]; then
  echo "--> Generating platform icon set..."
  bash "$ROOT/scripts/generate-icons.sh"
fi

# ── 4. Build Tauri native application and bundle ─────────────
echo "--> Building native release bundles via Tauri CLI..."
npx --yes @tauri-apps/cli build

# ── 5. Stage outputs in dist/ ────────────────────────────────
echo "--> Staging outputs to dist/..."
DIST="$ROOT/dist"
mkdir -p "$DIST"

RELEASE_BUNDLE="$ROOT/src-tauri/target/release/bundle"

# Copy App if on macOS and create clean standard DMG
if [[ -d "$RELEASE_BUNDLE/macos/Munger.app" ]]; then
  rm -rf "$DIST/Munger.app"
  cp -R "$RELEASE_BUNDLE/macos/Munger.app" "$DIST/"
  
  echo "--> Creating clean macOS disk image (.dmg)..."
  STAGE=$(mktemp -d)
  cp -R "$DIST/Munger.app" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  VERSION=$(grep '"version"' "$ROOT/src-tauri/tauri.conf.json" | head -1 | awk -F '"' '{print $4}')
  ARCH="$(uname -m)"
  hdiutil create -volname "Munger" -srcfolder "$STAGE" -ov -format UDZO "$DIST/Munger_${VERSION}_${ARCH}.dmg"
  rm -rf "$STAGE"
elif [[ -d "$RELEASE_BUNDLE/dmg" ]]; then
  cp -R "$RELEASE_BUNDLE/dmg"/*.dmg "$DIST/" 2>/dev/null || true
fi

# Copy Linux packages if present
if [[ -d "$RELEASE_BUNDLE/deb" ]]; then
  cp -R "$RELEASE_BUNDLE/deb"/*.deb "$DIST/" 2>/dev/null || true
fi
if [[ -d "$RELEASE_BUNDLE/appimage" ]]; then
  cp -R "$RELEASE_BUNDLE/appimage"/*.AppImage "$DIST/" 2>/dev/null || true
fi

# ── 6. Summary ───────────────────────────────────────────────
echo ""
echo "=================================================="
echo " Packaging Complete!"
echo "=================================================="
echo "Installable artifacts in dist/:"
ls -lh "$DIST"
echo ""

if command -v shasum &>/dev/null; then
  echo "SHA256 Checksums:"
  (cd "$DIST" && shasum -a 256 *.dmg 2>/dev/null || true)
  (cd "$DIST" && shasum -a 256 *.deb *.AppImage 2>/dev/null || true)
fi

echo ""
echo "To open the installer:"
echo "  open dist/*.dmg"
