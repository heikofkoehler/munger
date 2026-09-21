The core idea: VS Code's local-first model is workspace folder as source of truth + local engine + extensions + no account. Munger already has the engine (FastAPI backend) and the no-account part — it's missing the workspace, the shell, and the extension model.

The mapping

VS Code workspace folder → a Munger workspace folder on disk holding everything
Language server → your Python analytics backend (keep it, don't rewrite)
Extensions → data-source and analysis plugins
settings.json, command palette, auto-update → same

Phase 1 — Workspace and file-based truth (small)

Define a workspace layout, e.g. ~/Munger/<name>/: holdings/ (CSV/Parquet), snapshots/YYYY-MM-DD.json, settings.json, cache/
Every data refresh writes an immutable dated snapshot → portfolio history and time-travel for free, diffable with git
Files are the source of truth; SQLite stays as a derived cache underneath
This alone makes it genuinely local-first; everything after is feel and distribution

Phase 2 — Desktop shell (medium)

Tauri, not Electron: ~10MB vs ~150MB, Rust-based, uses the system webview. Your existing web UI drops in unchanged; the Python backend ships as a bundled sidecar binary via PyInstaller
Gets you: double-clickable app, menu bar/tray icon, native file dialogs, background refresh, built-in auto-updater — no terminal, no uvicorn incantation

Phase 3 — Offline-first sync engine (medium)

Each source (Monarch, Sheets, CSV, later Schwab/Plaid) becomes a sync adapter with visible status: last synced, stale, error
Syncs merge into local snapshots; the app works fully offline on cache
Scheduled background refresh, VS Code auto-save style

Phase 4 — Extensions (large, do last)

Plugin API with a manifest: source plugins and view/metric plugins, sandboxed
Sideload from a local extensions folder first; a marketplace is optional, not required
Command palette (Cmd+K) spanning core and extensions

Phase 5 — Polish

settings.json with schema plus a UI editor, VS Code style
Optional end-to-end-encrypted backup of settings/snapshots — or just document "point your workspace at Dropbox or git"
Portable mode: whole workspace on a USB stick

Explicitly not doing: rewriting the Python backend, adding accounts or auth, building any cloud component.

Suggested order is 1 → 2 → 3, then 4 and 5 as appetite allows. Phase 1 is the one that changes the architecture; the rest changes how it feels to use.