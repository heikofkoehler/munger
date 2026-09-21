"""
sidecar.py — PyInstaller entrypoint for the Tauri desktop app.

Starts the FastAPI backend on a free loopback port and prints
"MUNGER_PORT=<port>" as its first stdout line, so the Tauri shell can point
its window at the backend. The security posture is identical to
`uvicorn main:app`: only ticker symbols leave the machine (yfinance), no
telemetry, secrets stay in .env / the OS keychain.

Build with:  scripts/build-sidecar.sh
Run by:      the Tauri shell (src-tauri/src/main.rs); not for direct use,
             though `python sidecar.py` works for debugging the protocol.
"""

import socket
import sys


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    # Tauri reads the port from our stdout; flush so it never blocks on buffering.
    port = _free_port()
    print(f"MUNGER_PORT={port}", flush=True)

    try:
        from multiprocessing import freeze_support
        freeze_support()
    except ImportError:
        pass

    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
