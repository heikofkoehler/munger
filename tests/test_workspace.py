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
