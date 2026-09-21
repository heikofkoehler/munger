import os
from pathlib import Path

import pytest

from core.workspace import (
    ENV_VAR,
    ensure_workspace,
    is_inside_repo,
    repo_root,
    resolve_workspace,
)


def test_resolve_explicit_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from-env"))
    ws = resolve_workspace(explicit=str(tmp_path / "explicit"))
    assert ws == tmp_path / "explicit"


def test_resolve_env_over_default(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from-env"))
    assert resolve_workspace() == tmp_path / "from-env"


def test_resolve_default(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_workspace() == tmp_path / ".munger" / "default"


def test_ensure_workspace_creates_layout(tmp_path):
    ws = ensure_workspace(tmp_path / "ws")
    assert (ws / "snapshots").is_dir()
    assert (ws / "cache").is_dir()


def test_ensure_workspace_idempotent(tmp_path):
    ws = ensure_workspace(tmp_path / "ws")
    assert ensure_workspace(ws) == ws


def test_is_inside_repo(tmp_path):
    assert is_inside_repo(repo_root()) is True
    assert is_inside_repo(tmp_path) is False


def test_resolve_expands_user(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    # explicit "~" must expand; only check it no longer starts with "~"
    assert not str(resolve_workspace(explicit="~/somewhere")).startswith("~")


def test_cache_path_lives_in_workspace(tmp_path, monkeypatch):
    from core.workspace import cache_path

    monkeypatch.setenv(ENV_VAR, str(tmp_path / "ws"))
    p = cache_path("vanguard_voo_holdings.csv")
    assert p == tmp_path / "ws" / "cache" / "vanguard_voo_holdings.csv"


def test_resolve_cached_file_prefers_workspace_copy(tmp_path, monkeypatch):
    from core.workspace import resolve_cached_file

    monkeypatch.setenv(ENV_VAR, str(tmp_path / "ws"))
    ws_file = tmp_path / "ws" / "cache" / "f.csv"
    ws_file.parent.mkdir(parents=True)
    ws_file.write_text("workspace")
    legacy = tmp_path / "f.csv"
    legacy.write_text("legacy")
    monkeypatch.chdir(tmp_path)
    assert resolve_cached_file("f.csv") == ws_file


def test_resolve_cached_file_falls_back_to_legacy(tmp_path, monkeypatch):
    from core.workspace import cache_path, resolve_cached_file

    monkeypatch.setenv(ENV_VAR, str(tmp_path / "ws"))
    legacy = tmp_path / "f.csv"
    legacy.write_text("legacy")
    monkeypatch.chdir(tmp_path)
    assert resolve_cached_file("f.csv").resolve() == legacy.resolve()
    # writers still target the workspace path when nothing exists
    legacy.unlink()
    assert resolve_cached_file("f.csv") == cache_path("f.csv")


def test_user_file_prefers_cwd_then_workspace(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, ".")
    from data.sources import _user_file

    monkeypatch.setenv(ENV_VAR, str(tmp_path / "ws"))
    monkeypatch.chdir(tmp_path)
    # legacy CWD copy wins when present
    Path("token.json").write_text("{}")
    assert _user_file("token.json") == "token.json"
    # otherwise the workspace root is the stable home
    Path("token.json").unlink()
    assert _user_file("token.json") == str(tmp_path / "ws" / "token.json")
    # absolute paths pass through untouched
    assert _user_file("/etc/hosts") == "/etc/hosts"
