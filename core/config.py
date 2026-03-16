import os

def check_gitignore():
    """Verify .gitignore exists and contains required security patterns."""
    required = {"*.csv", "*.json", "*.env", "*.db"}
    gitignore_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".gitignore")

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
