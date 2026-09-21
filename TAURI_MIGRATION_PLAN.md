Autonomous Agent Execution Spec: Munger Web-to-Tauri Migration

This document serves as an end-to-end implementation plan for transforming the Munger portfolio analysis application into a cross-platform desktop application using Tauri.

Agent Operating Directives & Guardrails

1. Zero-Regression Invariant: The existing web mode (npm run dev, browser access) must remain functional at every commit. Do not delete web-specific implementations; wrap them behind interface abstractions.
2. Strict Verification Gate: Every phase must pass both web and desktop test suites before moving to the next.
  ⚬ Web gate: npm run lint && npm run test && npm run build
  ⚬ Tauri gate: cargo check --manifest-path src-tauri/Cargo.toml && cargo test --manifest-path src-tauri/Cargo.toml
3. Atomic PR / Commit Structure: Commit after each subtask with clear scopes (e.g., feat(core): introduce platform driver contracts).

Target Architecture

munger/
├── src/                          # Frontend UI Layer (React/TypeScript)
│   ├── platform/                 # Driver Abstraction Layer
│   │   ├── types.ts              # Unified domain interfaces
│   │   ├── index.ts              # Dynamic runtime driver resolver
│   │   ├── web/                  # Browser / HTTP implementations
│   │   └── tauri/                # Tauri invoke / IPC implementations
├── src-tauri/                    # Native Desktop Layer (Rust)
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── capabilities/             # Tauri v2 capability definitions
│   └── src/
│       ├── main.rs
│       ├── lib.rs                # Core entry point & command registration
│       ├── commands/             # Strongly typed IPC command handlers
│       │   ├── analytics.rs      # Financial math (XIRR, Drawdown, TWR)
│       │   ├── storage.rs        # SQLite query bridge
│       │   ├── secrets.rs        # Keyring / credential storage
│       │   └── market.rs         # Market data ingestion & caching
│       └── db/                   # Embedded SQLite migrations & models


Phase 1: Platform Driver Abstraction Layer

Objective

Decouple the frontend from direct HTTP/WebSocket calls, browser storage APIs, and local environment variables by introducing an abstracted driver boundary.

Tasks

1. Define Core Contracts:
   Create src/platform/types.ts:
  ⚬ StorageDriver: loadPortfolio(), saveTransaction(), getTransactions(filter)
  ⚬ AnalyticsDriver: computeXirr(txs), computeDrawdown(history), computeAllocation(holdings)
  ⚬ SecretDriver: getSecret(key), setSecret(key, val), deleteSecret(key)
  ⚬ DialogDriver: openFileDialog(options), saveFileDialog(options)
  ⚬ MarketDriver: fetchQuote(ticker), fetchHistorical(ticker, range)
2. Implement Existing Web Driver:
   Create src/platform/web/ implementing all interfaces using the existing HTTP client, localStorage/IndexedDB, and HTML file inputs.
3. Implement Runtime Provider Detection:
   Create src/platform/index.ts:
   export const isTauriEnvironment = (): boolean =>
     typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
   
   export function getPlatform(): PlatformDrivers {
     return isTauriEnvironment() ? tauriDrivers : webDrivers;
   }
   
4. Refactor UI Call Sites:
   Replace all direct fetch() calls or local storage access in state stores and components with calls to getPlatform().driverName.method().

Acceptance Criteria

⚬ npm run build succeeds.
⚬ Existing web test suite passes with 100% parity.
⚬ Web app behaves identically in a standard browser tab.

Phase 2: Tauri Core Scaffolding & Desktop Shell

Objective

Initialize the Tauri desktop shell around the application without modifying the existing backend runtime, validating UI rendering and packaging.

Tasks

1. Initialize Tauri CLI & Rust Project:
   npm install --save-dev @tauri-apps/cli @tauri-apps/api
   npx tauri init --app-name munger --window-title "Munger" \
     --dist-dir "../dist" --dev-url "http://localhost:5173"
   
2. Configure App Capabilities:
   Define src-tauri/capabilities/default.json with baseline execution rights:
  ⚬ Allow window creation and event listeners.
  ⚬ Deny arbitrary shell execution.
3. Verify Development Mode:
   Add npm script: "desktop:dev": "tauri dev". Ensure the app launches in a native OS Webview window (WebKit on macOS, WebView2 on Windows, WebKitGTK on Linux).

Acceptance Criteria

⚬ npm run desktop:dev boots the frontend inside a native desktop shell.
⚬ State management and UI rendering work identically to the web version.

Phase 3: Native OS Keyring & File System Drivers

Objective

Replace insecure browser local storage for sensitive API keys with OS-level secure storage and implement native system file dialogs.

Tasks

1. Add Native Dependencies to src-tauri/Cargo.toml:
   [dependencies]
   tauri = { version = "2", features = [] }
   serde = { version = "1.0", features = ["derive"] }
   serde_json = "1.0"
   keyring = "2.1"
   tauri-plugin-dialog = "2"
   tauri-plugin-fs = "2"
   
2. Implement Rust Secret Commands:
   Create src-tauri/src/commands/secrets.rs exposing:
  ⚬ #[tauri::command] fn set_api_key(service: String, key: String) -> Result<(), String>
  ⚬ #[tauri::command] fn get_api_key(service: String) -> Result<Option<String>, String>
  ⚬ #[tauri::command] fn delete_api_key(service: String) -> Result<(), String>
    Store credentials under the service namespace com.munger.desktop.
3. Implement Tauri Frontend Drivers:
   Create src/platform/tauri/secrets.ts and src/platform/tauri/dialog.ts routing calls through @tauri-apps/api/core::invoke and @tauri-apps/plugin-dialog.

Acceptance Criteria

⚬ Running in the browser: API keys store in fallback storage (IndexedDB/localStorage) and file imports use <input type="file">.
⚬ Running in desktop: API keys save directly to macOS Keychain / Windows Credential Manager / Linux Secret Service, and file imports use native OS dialogs.

Phase 4: Native Financial Computation Engine (Rust)

Objective

Port performance-critical financial math (XIRR, Time-Weighted Returns, historical drawdowns) to Rust for multi-threaded, bare-metal execution.

Tasks

1. Implement Mathematical Core:
   Create src-tauri/src/commands/analytics.rs:
  ⚬ Implement the Newton-Raphson algorithm for XIRR computation:
    
    $$P(r) = \sum_{i=1}^{n} \frac{C_i}{(1 + r)^{\frac{d_i - d_0}{365}}} = 0$$
  ⚬ Add bracket checks and fallback to bisection method when Newton-Raphson diverges.
  ⚬ Implement Max Drawdown, CAGR, and Sharpe Ratio calculations.
2. Add Golden Master Unit Tests:
   Create src-tauri/src/commands/analytics_tests.rs:
  ⚬ Include standard benchmark transaction datasets (e.g., cash dividends, irregular cash injections, multi-year flat returns).
  ⚬ Verify floating-point parity within an epsilon of 10^{-6}.
3. Implement Parity Shadowing in Frontend:
   In non-production builds, have src/platform/tauri/analytics.ts execute both the legacy TypeScript math and the native Rust math, logging any divergence greater than 0.01%.

Acceptance Criteria

⚬ cargo test passes all financial analytics test vectors.
⚬ Shadow verification reports zero mathematical discrepancies between web and native calculation results.

Phase 5: Market Ingestion & Network Decoupling

Objective

Bypass browser CORS restrictions and eliminate local proxy servers by moving financial market data ingestion to native Rust background routines.

Tasks

1. Add HTTP & Async Crates to src-tauri/Cargo.toml:
   reqwest = { version = "0.12", features = ["json"] }
   tokio = { version = "1", features = ["full"] }
   chrono = { version = "0.4", features = ["serde"] }
   
2. Implement Market Ingestion Commands:
   Create src-tauri/src/commands/market.rs:
  ⚬ Handle quote fetching, historical price time-series downloads, and API rate-limiting.
  ⚬ Standardize market API responses into uniform Rust structs:
    #[derive(Serialize, Deserialize)]
    pub struct MarketQuote {
        pub ticker: String,
        pub price: f64,
        pub change_percent: f64,
        pub timestamp: i64,
    }
    
3. Wire Frontend Market Driver:
   Implement src/platform/tauri/market.ts using invoke('fetch_market_quote', { ticker }).

Acceptance Criteria

⚬ Quotes and historical series fetch successfully in desktop mode without requiring a local Node/Python reverse proxy.
⚬ Rate limits return structured errors handled gracefully by the UI.

Phase 6: Embedded Database & Local Persistence

Objective

Replace local-server or browser-bound persistence with an embedded, zero-configuration SQLite database running in Rust.

Tasks

1. Integrate rusqlite:
   Add rusqlite = { version = "0.31", features = ["bundled"] } to src-tauri/Cargo.toml.
2. Implement Database Initialization & Migrations:
   Create src-tauri/src/db/mod.rs:
  ⚬ Initialize munger.db inside the system's designated application data directory:
    ⚬ macOS: ~/Library/Application Support/com.munger.desktop/
    ⚬ Linux: ~/.config/com.munger.desktop/
    ⚬ Windows: %APPDATA%\com.munger.desktop\
  ⚬ Create versioned migration tables (001_init.sql, 002_transactions.sql).
3. Data Import Bridge:
   Implement a one-time import utility: on first boot, if the embedded database is empty, check for legacy exports (portfolio.json or .csv) and prompt the user to migrate historical data.
4. Expose Storage Commands:
   Create src-tauri/src/commands/storage.rs to handle transaction inserts, updates, and portfolio aggregate queries.

Acceptance Criteria

⚬ Data persists across application restarts in native OS data directories.
⚬ Database operations execute asynchronously without blocking the UI thread.

Phase 7: Packaging, CI/CD, and Validation

Objective

Automate multi-platform compilation, notarization, and continuous verification.

Tasks

1. Configure Production Bundling:
   Update src-tauri/tauri.conf.json:
  ⚬ Set bundle icons for macOS (.icns), Windows (.ico), and Linux (.png).
  ⚬ Configure window constraints: min-width 900px, min-height 600px.
2. Implement GitHub Actions Workflow:
   Create .github/workflows/desktop-release.yml:
  ⚬ Matrix build across macos-latest, windows-latest, and ubuntu-latest.
  ⚬ Run web test suite, Rust test suite, and run tauri-apps/tauri-action to build .dmg, .msi, and .AppImage.
3. Code Signing Setup:
   Configure environment variables in CI for Apple Developer ID notarization (APPLE_CERTIFICATE, APPLE_API_KEY) and Windows Authenticode signing.

Acceptance Criteria

⚬ GitHub Actions pipeline passes end-to-end on all three target operating systems.
⚬ Production artifacts install and execute cleanly without runtime security warnings.