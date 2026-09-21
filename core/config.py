import os

from core.workspace import repo_root


def check_gitignore():
    """
    Verify .gitignore exists and contains required security patterns.

    When running from a source checkout the check is enforced: secrets and
    data files (.env, *.csv, *.json, *.db) must not be committable. When
    running as an installed app (no .git directory, e.g. a future Tauri
    bundle), there is no git checkout to protect, so the check is skipped
    instead of crashing the app.
    """
    gitignore_path = os.path.join(repo_root(), ".gitignore")
    required = {"*.csv", "*.json", "*.env", "*.db"}

    if not os.path.exists(gitignore_path):
        if os.path.isdir(os.path.join(repo_root(), ".git")):
            raise RuntimeError(".gitignore not found — refusing to start. "
                               "Create .gitignore with: *.csv, *.json, *.env, *.db")
        return

    with open(gitignore_path) as f:
        lines = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    missing = required - lines
    if missing:
        raise RuntimeError(
            f".gitignore is missing required patterns: {sorted(missing)}. "
            "Add them before running."
        )
