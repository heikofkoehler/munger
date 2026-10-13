"""
sidecar.py — PyInstaller entrypoint for the Tauri desktop app.

Starts the FastAPI backend on a free loopback port and prints
"MUNGER_PORT=<port>" on stdout, so the Tauri shell can point its window
at the backend. The security posture is identical to
`uvicorn main:app`: only ticker symbols leave the machine (yfinance), no
telemetry, secrets stay in .env / the OS keychain.

The port is announced only after uvicorn is actually accepting
connections. Announcing it earlier races the Tauri window: the window
opens immediately on the announced port, and if the server isn't
listening yet (frozen-binary startup plus the data-cache build inside
`main` take seconds), the page loads into a connection-refused blank
window that never retries.

Build with:  scripts/build-sidecar.sh
Run by:      the Tauri shell (src-tauri/src/main.rs); not for direct use,
             though `python sidecar.py` works for debugging the protocol.
"""

import sys
import threading
import time


def main() -> None:
    try:
        from multiprocessing import freeze_support
        freeze_support()
    except ImportError:
        pass

    # Import the app object directly (not "main:app") so PyInstaller's
    # static analysis bundles main.py and its core/data/metrics packages.
    # A string import would fail at runtime in the frozen binary.
    # Note: importing main builds the data cache, which takes a few
    # seconds — the port below is announced only after the server is
    # listening, so this cost never races the window.
    from main import app
    import uvicorn

    # port=0 lets the OS pick a free port at bind time: no probe-then-bind
    # race, no chance of losing the port between choosing and binding.
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    thread.start()

    # server.servers is assigned (and populated with listening sockets) only
    # inside Server.startup(), so poll with getattr until it appears.
    def _listening_port() -> int | None:
        servers = getattr(server, "servers", None)
        if servers:
            return servers[0].sockets[0].getsockname()[1]
        return None

    deadline = time.time() + 30
    port = None
    while port is None:
        if not thread.is_alive():
            print("munger-backend: server thread died during startup", file=sys.stderr)
            sys.exit(1)
        if time.time() > deadline:
            print("munger-backend: timed out waiting for server startup", file=sys.stderr)
            sys.exit(1)
        port = _listening_port()
        if port is None:
            time.sleep(0.05)

    # Tauri reads the port from our stdout; flush so it never blocks on buffering.
    print(f"MUNGER_PORT={port}", flush=True)

    thread.join()


if __name__ == "__main__":
    main()
