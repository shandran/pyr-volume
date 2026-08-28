import os
from pathlib import Path

from dotenv import load_dotenv


def load_cave_token(project_root):
    """Load CAVECLIENT_TOKEN without exposing dotenv filesystem paths."""
    env_token = os.getenv("CAVECLIENT_TOKEN")
    if env_token:
        return env_token, "environment"

    project_root = Path(project_root).expanduser().resolve()
    override_path = os.getenv("PYR_DOTENV_PATH")

    if override_path:
        dotenv_path = Path(override_path).expanduser()
        if not dotenv_path.is_absolute():
            dotenv_path = project_root / dotenv_path
        dotenv_path = dotenv_path.resolve()
        if not dotenv_path.exists():
            raise RuntimeError("PYR_DOTENV_PATH is set, but the referenced dotenv file does not exist.")
        load_dotenv(dotenv_path=dotenv_path, override=False)
        token = os.getenv("CAVECLIENT_TOKEN")
        if token:
            return token, "PYR_DOTENV_PATH"
        _raise_missing_token()

    dotenv_candidates = [
        (project_root.parent / ".gitignore" / ".env", "sibling dotenv"),
        (project_root / ".env", "project dotenv"),
    ]

    for dotenv_path, source_label in dotenv_candidates:
        if dotenv_path.exists():
            load_dotenv(dotenv_path=dotenv_path, override=False)
            token = os.getenv("CAVECLIENT_TOKEN")
            if token:
                return token, source_label

    _raise_missing_token()


def _raise_missing_token():
    raise RuntimeError(
        "CAVECLIENT_TOKEN was not found. Supported options: set CAVECLIENT_TOKEN directly; "
        "set PYR_DOTENV_PATH; use the sibling .gitignore/.env location; or place .env in project_root."
    )
