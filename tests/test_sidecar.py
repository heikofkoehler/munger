"""
tests/test_sidecar.py — regression test for the blank-window bug.

The Tauri shell opens its window on the announced port immediately, so
sidecar.py must print MUNGER_PORT only *after* uvicorn is accepting
connections (fixed in 5248743). This test runs the real
`python sidecar.py`, waits for the announce line, then proves the port is
live with an HTTP request.

Takes a few seconds (interpreter + cache build + uvicorn startup). This
exercises the `python sidecar.py` path, not the frozen PyInstaller binary.
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_sidecar_announces_port_only_after_listening():
    ws = tempfile.mkdtemp(prefix="munger-sidecar-test-")
    env = {**os.environ, "MUNGER_WORKSPACE": ws}
    # keep the import-time cache build offline and fast
    for var in ("MONARCH_JSON_PATH", "CSV_PATH", "SHEET_ID"):
        env.pop(var, None)

    proc = subprocess.Popen(
        [sys.executable, "sidecar.py"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        port = None
        deadline = time.time() + 90
        while time.time() < deadline and port is None:
            if proc.poll() is not None:
                _, stderr = proc.communicate()
                raise AssertionError(f"sidecar exited early: {stderr[-2000:]}")
            line = proc.stdout.readline()  # EOF when the process exits
            m = re.match(r"^MUNGER_PORT=(\d+)$", line.strip())
            if m:
                port = int(m.group(1))
        assert port is not None, "sidecar never announced MUNGER_PORT"

        # The core assertion: the announced port must already serve HTTP.
        url = f"http://127.0.0.1:{port}/"
        for _ in range(50):
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    assert resp.status == 200
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError(f"port {port} not serving HTTP after announce")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
