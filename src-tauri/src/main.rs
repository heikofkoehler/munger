#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Munger desktop shell.
//!
//! Architecture: the Python backend (FastAPI) runs as a Tauri *sidecar*
//! binary (`munger-backend`, built by scripts/build-sidecar.sh). The shell
//! spawns it, reads the loopback port it announces on stdout
//! ("MUNGER_PORT=<port>"), and opens the main window pointed at the backend —
//! which serves both the API and the existing static/index.html frontend.
//! No analytics code is duplicated in Rust; Python remains the single
//! engine. When the window closes, the sidecar is killed and the app exits.

use std::sync::Mutex;

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Handle to the Python backend sidecar, so we can shut it down with the app.
struct BackendState(Mutex<Option<CommandChild>>);

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendState(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            // Spawn the backend without blocking setup; the window opens once
            // the sidecar announces its port.
            tauri::async_runtime::spawn(async move {
                if let Err(e) = run_backend(handle).await {
                    eprintln!("munger: backend failed to start: {e}");
                    std::process::exit(1);
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // Single-window utility: closing the window stops the backend
            // and quits the app (also on macOS, where close != quit).
            if window.label() == "main" {
                if let WindowEvent::Destroyed = event {
                    if let Some(state) = window.app_handle().try_state::<BackendState>() {
                        if let Some(mut child) = state.0.lock().unwrap().take() {
                            let _ = child.kill();
                        }
                    }
                    window.app_handle().exit(0);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running munger");
}

async fn run_backend(app: tauri::AppHandle) -> Result<(), String> {
    let (mut rx, child) = app
        .shell()
        .sidecar("munger-backend")
        .map_err(|e| format!("sidecar lookup failed: {e}"))?
        .spawn()
        .map_err(|e| format!("sidecar spawn failed: {e}"))?;

    // The sidecar prints "MUNGER_PORT=<port>" as its first stdout line.
    let mut port: Option<String> = None;
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                if let Some(p) = String::from_utf8_lossy(&line).trim().strip_prefix("MUNGER_PORT=") {
                    port = Some(p.to_string());
                    break;
                }
            }
            CommandEvent::Stderr(line) => {
                eprintln!(
                    "munger-backend: {}",
                    String::from_utf8_lossy(&line).trim_end()
                );
            }
            CommandEvent::Terminated(payload) => {
                return Err(format!(
                    "sidecar terminated before announcing a port: {payload:?}"
                ));
            }
            _ => {}
        }
    }
    let port = port.ok_or("sidecar closed stdout without announcing a port")?;

    // Remember the child so the window-close handler can kill it.
    if let Some(state) = app.try_state::<BackendState>() {
        state.0.lock().unwrap().replace(child);
    }

    // Keep draining sidecar output in the background so its pipe never blocks.
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            if let CommandEvent::Stderr(line) = event {
                eprintln!(
                    "munger-backend: {}",
                    String::from_utf8_lossy(&line).trim_end()
                );
            }
        }
    });

    WebviewWindowBuilder::new(
        &app,
        "main",
        WebviewUrl::External(format!("http://127.0.0.1:{port}/").parse().unwrap()),
    )
    .title("Munger")
    .inner_size(1280.0, 860.0)
    .min_inner_size(900.0, 600.0)
    .build()
    .map_err(|e| format!("window creation failed: {e}"))?;

    Ok(())
}
