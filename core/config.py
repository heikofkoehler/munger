import os

from core.workspace import is_inside_repo, repo_root


def check_gitignore(workspace=None):
    """
    Verify .gitignore exists and contains required security patterns.

    Only enforced when the workspace lives inside the repository: with the
    default workspace (~/.munger) there is nothing sensitive under git, so
    the check is skipped.
    """
    if not is_inside_repo(workspace if workspace is not None else repo_root()):
        return

    required = {"*.csv", "*.json", "*.env", "*.db"}
    gitignore_path = os.path.join(repo_root(), ".gitignore")

    if not os.path.exists(gitignore_path):
        raise RuntimeError(".gitignore not found — refusing to start. "
                           "Create .gitignore with: *.csv, *.json, *.env, *.db")

    with open(gitignore_path) as f:
        lines = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    missing = required - lines
    if missing:
        raise RuntimeError(
            f".gitignore is missing required patterns: {sorted(missing)}. "
            "Add them before running."
        )
