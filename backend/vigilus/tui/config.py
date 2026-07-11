"""Token and server config storage for the Vigilus TUI.

Stored at ``~/.config/vigilus/tui.json`` with 0600 permissions so the
bearer token is not world-readable.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "vigilus"
CONFIG_FILE = CONFIG_DIR / "tui.json"


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)


def get_token() -> str | None:
    """Return the stored bearer token if it has not expired."""
    cfg = load_config()
    token = cfg.get("token")
    expires = cfg.get("expires_at")
    if not token or not expires:
        return None
    try:
        exp = datetime.fromisoformat(expires)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp > datetime.now(UTC):
            return token
    except Exception:
        pass
    return None


def save_token(token: str, expires_at: str, username: str, base_url: str) -> None:
    cfg = load_config()
    cfg.update(
        {"token": token, "expires_at": expires_at, "username": username, "base_url": base_url}
    )
    save_config(cfg)


def clear_token() -> None:
    cfg = load_config()
    cfg.pop("token", None)
    cfg.pop("expires_at", None)
    save_config(cfg)


def get_base_url() -> str:
    return load_config().get("base_url", "http://localhost:8000")


def get_username() -> str | None:
    return load_config().get("username")
