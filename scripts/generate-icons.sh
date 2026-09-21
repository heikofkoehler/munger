#!/usr/bin/env bash
#
# Generate the full platform icon set (.icns, .ico, sized PNGs) from
# src-tauri/icons/icon.png using the Tauri CLI. Run once on any machine
# with Node.js; commit the results.
#
# After regenerating, expand the "icon" array in src-tauri/tauri.conf.json
# to reference the new files (see https://v2.tauri.app/reference/config/#icon).
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

npx --yes @tauri-apps/cli@2 icon src-tauri/icons/icon.png
