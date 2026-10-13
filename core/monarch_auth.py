"""
monarch_auth.py — Browser-based authentication & credential persistence for Monarch Money.

Provides:
- persist_env_var: Safely updates or appends key=value pairs in .env and os.environ.
- get_monarch_status: Returns current Monarch session credentials & last sync metadata.
- MonarchBrowserAuth: Background worker that opens Google Chrome, allows user
  to complete Google SSO / email login, extracts cookies, and syncs portfolio.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def get_env_file_path(base_dir: Optional[Path] = None) -> Path:
    """Find the active .env file in base_dir, current directory, or repo root."""
    if base_dir and (base_dir / ".env").exists():
        return base_dir / ".env"
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    repo_env = Path(__file__).resolve().parent.parent / ".env"
    if repo_env.exists():
        return repo_env
    return cwd_env


def persist_env_var(key: str, value: str, env_path: Optional[Path] = None) -> None:
    """
    Persist an environment variable to the .env file and update os.environ.
    Preserves existing lines, comments, and structure.
    """
    if env_path is None:
        env_path = get_env_file_path()

    os.environ[key] = value

    lines = []
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning(f"Could not read existing .env file {env_path}: {e}")

    escaped_val = value.replace('"', '\\"')
    target_entry = f'{key}="{escaped_val}"\n'

    found_idx = -1
    comment_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            found_idx = i
            break
        elif stripped.startswith(f"#{key}=") or stripped.startswith(f"# {key}="):
            if comment_idx == -1:
                comment_idx = i

    if found_idx != -1:
        lines[found_idx] = target_entry
    elif comment_idx != -1:
        lines[comment_idx] = target_entry
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(target_entry)

    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        logger.info(f"Updated {key} in {env_path}")
    except Exception as e:
        logger.error(f"Failed to write to .env file {env_path}: {e}")


def get_monarch_status(response_path: str = "monarch_response.json") -> dict:
    """Check current Monarch credentials and local response status."""
    has_cookie = bool(os.environ.get("MONARCH_COOKIE"))
    has_token = bool(os.environ.get("MONARCH_TOKEN"))
    auth_type = "cookie" if has_cookie else ("token" if has_token else None)

    status = {
        "has_credentials": has_cookie or has_token,
        "auth_type": auth_type,
        "last_sync": None,
        "total_value": None,
        "total_basis": None,
        "total_gain": None,
        "holdings_count": 0,
        "accounts_count": 0,
        "securities_count": 0,
        "file_exists": False,
    }

    resp_file = Path(response_path)
    if resp_file.exists():
        try:
            status["file_exists"] = True
            mtime = resp_file.stat().st_mtime
            status["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
            with open(resp_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            portfolio = data.get("data", {}).get("portfolio", {})
            perf = portfolio.get("performance", {})
            if perf:
                status["total_value"] = perf.get("totalValue")
                status["total_basis"] = perf.get("totalBasis")
                status["total_gain"] = perf.get("totalChangeDollars")

            edges = portfolio.get("aggregateHoldings", {}).get("edges", [])
            holdings_count = 0
            accounts = set()
            securities = set()
            for edge in edges:
                node = edge.get("node", {})
                sec = node.get("security", {})
                if sec.get("id"):
                    securities.add(sec["id"])
                for h in node.get("holdings", []):
                    holdings_count += 1
                    acct = h.get("account", {})
                    if acct.get("id"):
                        accounts.add(acct["id"])
            status["holdings_count"] = holdings_count
            status["accounts_count"] = len(accounts)
            status["securities_count"] = len(securities)
        except Exception as e:
            logger.warning(f"Error inspecting {response_path}: {e}")

    return status


def extract_chrome_monarch_cookies(profile_name: Optional[str] = None) -> str:
    """
    Extracts Monarch cookies (session_id and csrftoken) from the user's installed
    macOS Google Chrome profile using the macOS Keychain and SQLite.
    """
    import shutil
    import sqlite3
    import subprocess
    import tempfile
    import sys

    if sys.platform != "darwin":
        raise NotImplementedError("Direct Chrome cookie extraction is currently supported on macOS.")

    chrome_root = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    if not chrome_root.exists():
        raise FileNotFoundError(f"Chrome directory not found at {chrome_root}")

    # Determine candidate profile directories to search
    if profile_name:
        candidate_profiles = [profile_name]
    else:
        discovered = [p.name for p in sorted(chrome_root.glob("Profile *")) if p.is_dir()]
        candidate_profiles = ["Default"] + discovered

    # Query keychain for 'Chrome Safe Storage'
    cmd = [
        "security",
        "find-generic-password",
        "-w",
        "-s",
        "Chrome Safe Storage",
        "-a",
        "Chrome",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        raise TimeoutError(
            "Keychain access timed out waiting for approval. Please click Allow when macOS prompts for Chrome Safe Storage."
        )

    if res.returncode != 0:
        raise PermissionError(
            f"Keychain access was denied or failed: {res.stderr.strip() or 'User cancelled prompt'}"
        )

    password = res.stdout.strip()
    if not password:
        raise ValueError("Empty password retrieved from Keychain for Chrome Safe Storage.")

    import hashlib
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    salt = b"saltysalt"
    iv = b" " * 16
    key = hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), salt, 1003, 16)

    last_error = None
    for prof in candidate_profiles:
        prof_dir = chrome_root / prof
        if not prof_dir.is_dir():
            continue

        cookie_paths = [
            prof_dir / "Cookies",
            prof_dir / "Network" / "Cookies",
        ]
        cookie_file = next((cp for cp in cookie_paths if cp.exists()), None)
        if not cookie_file:
            continue

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            temp_db = tf.name

        try:
            shutil.copy2(cookie_file, temp_db)
            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, value, encrypted_value FROM cookies WHERE host_key LIKE '%monarch%'"
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                continue

            cookies = {}
            for name, val, enc_val in rows:
                if val:
                    cookies[name] = val
                elif enc_val:
                    if enc_val[:3] == b"v10":
                        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
                        decryptor = cipher.decryptor()
                        decrypted = decryptor.update(enc_val[3:]) + decryptor.finalize()
                        padding_len = decrypted[-1]
                        if padding_len <= 16:
                            decrypted = decrypted[:-padding_len]
                        cookies[name] = decrypted.decode("utf-8", errors="ignore")

            if "session_id" in cookies and "csrftoken" in cookies:
                cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
                logger.info(f"Successfully extracted Monarch session cookies from Chrome profile '{prof}'.")
                return cookie_str
            else:
                last_error = f"Found cookies in profile '{prof}' ({list(cookies.keys())}), but missing active session_id or csrftoken."
        finally:
            if os.path.exists(temp_db):
                os.remove(temp_db)

    if last_error:
        raise ValueError(f"{last_error} Please ensure you are logged into monarchmoney.com in Chrome.")
    raise ValueError("No active Monarch cookies found in Chrome. Please log into Monarch in Chrome first.")


class MonarchBrowserAuth:
    """Manages an interactive Chrome login session via Playwright."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._browser = None
        self._state = {
            "status": "idle",  # "idle" | "running" | "success" | "error" | "cancelled"
            "message": "Ready to connect.",
            "error": None,
            "started_at": None,
            "finished_at": None,
        }

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def cancel(self) -> dict:
        with self._lock:
            if self._state["status"] == "running":
                self._cancel_event.set()
                self._state["status"] = "cancelled"
                self._state["message"] = "Login was cancelled."
                self._state["finished_at"] = time.time()
                # Close browser if active
                if self._browser:
                    try:
                        self._browser.close()
                    except Exception:
                        pass
                    self._browser = None
                return {"status": "cancelled", "message": "Login cancelled."}
            return {"status": self._state["status"], "message": self._state["message"]}

    def start(
        self,
        on_success_callback: Optional[Callable[[str], None]] = None,
        timeout_seconds: int = 180,
    ) -> dict:
        with self._lock:
            if self._state["status"] == "running":
                return {
                    "started": False,
                    "status": "running",
                    "message": "A browser login session is already in progress.",
                }

            self._cancel_event.clear()
            self._state = {
                "status": "running",
                "message": "Launching Google Chrome for Monarch login...",
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }

            self._thread = threading.Thread(
                target=self._run_browser_worker,
                args=(on_success_callback, timeout_seconds),
                daemon=True,
            )
            self._thread.start()
            return {"started": True, "status": "running", "message": self._state["message"]}

    def _run_browser_worker(
        self,
        on_success_callback: Optional[Callable[[str], None]],
        timeout_seconds: int,
    ) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            with self._lock:
                self._state["status"] = "error"
                self._state["error"] = "Playwright is not installed."
                self._state["message"] = "Playwright is not installed. Please install it in your environment."
                self._state["finished_at"] = time.time()
            return

        try:
            with sync_playwright() as p:
                profile_dir = Path.home() / ".munger" / "browser_profile"
                profile_dir.mkdir(parents=True, exist_ok=True)

                context = None
                launch_errors = []
                for launch_kwargs in [
                    {
                        "user_data_dir": str(profile_dir),
                        "channel": "chrome",
                        "headless": False,
                        "args": [
                            "--disable-blink-features=AutomationControlled",
                            "--no-first-run",
                            "--no-default-browser-check",
                        ],
                        "ignore_default_args": ["--enable-automation"],
                    },
                    {
                        "user_data_dir": str(profile_dir),
                        "headless": False,
                        "args": [
                            "--disable-blink-features=AutomationControlled",
                            "--no-first-run",
                            "--no-default-browser-check",
                        ],
                        "ignore_default_args": ["--enable-automation"],
                    },
                ]:
                    try:
                        context = p.chromium.launch_persistent_context(**launch_kwargs)
                        break
                    except Exception as le:
                        launch_errors.append(str(le))

                if not context:
                    raise RuntimeError(f"Could not launch browser: {'; '.join(launch_errors)}")

                self._browser = context
                page = context.pages[0] if context.pages else context.new_page()

                with self._lock:
                    self._state["message"] = "Chrome is open. Navigating to Monarch login..."

                page.goto("https://app.monarch.com/login")

                with self._lock:
                    self._state["message"] = (
                        "Monarch login page ready. Log in once; your session will be saved permanently."
                    )

                start_time = time.time()
                captured_cookie_str = None

                while time.time() - start_time < timeout_seconds:
                    if self._cancel_event.is_set():
                        with self._lock:
                            self._state["status"] = "cancelled"
                            self._state["message"] = "Login was cancelled."
                            self._state["finished_at"] = time.time()
                        try:
                            browser.close()
                        except Exception:
                            pass
                        self._browser = None
                        return

                    try:
                        # If page or browser closed by user, exit gracefully
                        if page.is_closed():
                            with self._lock:
                                self._state["status"] = "cancelled"
                                self._state["message"] = "Browser window was closed before login completed."
                                self._state["finished_at"] = time.time()
                            self._browser = None
                            return

                        cookies = context.cookies(["https://app.monarch.com", "https://api.monarch.com"])
                        cookie_dict = {c["name"]: c["value"] for c in cookies}

                        # Check if session_id and csrftoken exist
                        if "session_id" in cookie_dict and "csrftoken" in cookie_dict:
                            current_url = page.url
                            # Ensure we have left login/auth flows
                            if "/login" not in current_url and "/auth" not in current_url:
                                cookie_parts = [f"{c['name']}={c['value']}" for c in cookies]
                                captured_cookie_str = "; ".join(cookie_parts)
                                logger.info(f"Captured Monarch session cookies! Current URL: {current_url}")
                                break
                    except Exception as loop_e:
                        logger.debug(f"Cookie check loop exception: {loop_e}")
                        # Browser might have closed
                        if page.is_closed():
                            break

                    time.sleep(1)

                try:
                    browser.close()
                except Exception:
                    pass
                self._browser = None

                if not captured_cookie_str:
                    with self._lock:
                        if self._state["status"] != "cancelled":
                            self._state["status"] = "error"
                            self._state["error"] = "Timeout"
                            self._state["message"] = (
                                f"Login timed out after {timeout_seconds}s without authentication."
                            )
                            self._state["finished_at"] = time.time()
                    return

                with self._lock:
                    self._state["message"] = "Login detected! Saving session and syncing portfolio..."

                # Persist to .env
                persist_env_var("MONARCH_COOKIE", captured_cookie_str)

                # Trigger callback (fetch portfolio and rebuild cache)
                if on_success_callback:
                    try:
                        on_success_callback(captured_cookie_str)
                    except Exception as cb_err:
                        logger.error(f"Error in on_success_callback: {cb_err}", exc_info=True)
                        with self._lock:
                            self._state["status"] = "error"
                            self._state["error"] = str(cb_err)
                            self._state["message"] = f"Logged in, but portfolio sync failed: {cb_err}"
                            self._state["finished_at"] = time.time()
                        return

                with self._lock:
                    self._state["status"] = "success"
                    self._state["message"] = "Logged in and synced portfolio successfully!"
                    self._state["finished_at"] = time.time()

        except Exception as e:
            logger.error(f"Browser worker failure: {e}", exc_info=True)
            with self._lock:
                self._state["status"] = "error"
                self._state["error"] = str(e)
                self._state["message"] = f"Browser login failed: {e}"
                self._state["finished_at"] = time.time()


# Global singleton instance
browser_auth = MonarchBrowserAuth()
