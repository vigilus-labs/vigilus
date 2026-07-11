"""Vigilus configuration via environment variables."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

_BANNED_SECRETS = frozenset(
    {
        "changeme",
        "secret",
        "default",
        "change_me",
        "CHANGE_ME",
        "password",
        "PASSWORD",
        "",
    }
)


class Settings(BaseSettings):
    """Application settings loaded from VIGILUS_* environment variables."""

    model_config = {
        "env_prefix": "VIGILUS_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # ── Security ────────────────────────────────────────────
    secret_key: str = Field(validation_alias="VIGILUS_SECRET")

    # ── Database ────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./data/vigilus.db"

    # ── Paths ───────────────────────────────────────────────
    data_dir: str = "./data"

    # ── Server ──────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── RBAC / Trust ────────────────────────────────────────
    default_trust_mode: Literal["strict", "lenient"] = "strict"

    # ── JIT ─────────────────────────────────────────────────
    jit_max_ttl_minutes: int = 60
    jit_default_ttl_minutes: int = 15
    # How long a denied tool call waits for the user to approve/deny the
    # JIT request before giving up (seconds). The whole delegation pauses.
    jit_wait_seconds: int = 180
    # Same, but for unattended runs (scheduled tasks): nobody is watching the
    # chat, so the global JIT banner / channels need a longer window for a
    # human to approve before we fail closed. Never auto-grants.
    jit_wait_seconds_unattended: int = 1800

    # ── Task execution ──────────────────────────────────────
    # Bound an individual provider request so a stalled upstream cannot leave
    # an in-memory running task permanently stuck.
    llm_request_timeout_seconds: int = Field(default=120, gt=0)

    # ── CORS ────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:5173"]

    # ── Auth ────────────────────────────────────────────────
    auth_token_ttl_hours: int = 168  # 7 days
    auth_cookie_name: str = "vigilus_token"
    auth_cookie_secure: bool = False  # set True behind HTTPS/reverse proxy
    auth_max_login_failures: int = 5
    auth_lockout_minutes: int = 5

    # ── Channels (gateway) ───────────────────────────────────
    channels_enabled: bool = True  # master switch for the gateway
    telegram_bot_token: str | None = None  # env fallback; DB config wins
    discord_bot_token: str | None = None  # env fallback; DB config wins

    # ── Search / research ───────────────────────────────────
    # Vigilus-only web search + page fetch. Operators never get web tools.
    search_enabled: bool = True
    search_backend: Literal["searxng", "firecrawl"] = "searxng"
    fetch_backend: Literal["builtin", "firecrawl"] = "builtin"
    searxng_url: str | None = None  # e.g. http://searxng.lan:8080
    firecrawl_api_key: str | None = None  # env fallback; DB config wins
    search_max_results: int = 5
    web_fetch_max_bytes: int = 2_000_000  # builtin fetcher: hard download cap
    web_fetch_timeout_seconds: int = 15
    search_rate_limit_per_min: int = 20  # per session (Vigilus principal)
    # SSRF policy (decision #4: private fetch OFF)
    web_fetch_allow_private: bool = False  # block RFC1918/loopback/metadata
    web_fetch_allowed_schemes: list[str] = ["http", "https"]

    # ── Updates ─────────────────────────────────────────────
    # Outbound check against the published GitHub releases. Opt-out for
    # privacy; the only network call is an unauthenticated GET to api.github.com.
    update_check_enabled: bool = Field(default=True, validation_alias="VIGILUS_UPDATE_CHECK")
    update_repo: str = "vigilus-labs/vigilus"  # owner/name for releases + GHCR image
    update_check_interval_hours: int = 6  # in-process cache TTL

    # ── Logging ─────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Validators ──────────────────────────────────────────
    @field_validator("secret_key")
    @classmethod
    def _validate_secret_key(cls, v: str) -> str:
        if v.strip() in _BANNED_SECRETS:
            raise ValueError(
                "VIGILUS_SECRET must be set to a strong, unique value. "
                f"Banned values: {_BANNED_SECRETS}"
            )
        if len(v) < 8:
            raise ValueError("VIGILUS_SECRET must be at least 8 characters long.")
        return v

    @model_validator(mode="after")
    def _ensure_data_dir_set(self) -> Settings:
        return self

    # ── Derived helpers ─────────────────────────────────────
    @property
    def fernet_key(self) -> bytes:
        """Derive a 32-byte Fernet key from the secret_key."""
        digest = hashlib.sha256(self.secret_key.encode()).digest()
        return base64.urlsafe_b64encode(digest)

    @property
    def jwt_key(self) -> str:
        """Derive a JWT signing key from secret_key, domain-separated from fernet_key."""
        return hashlib.sha256(f"{self.secret_key}:jwt".encode()).hexdigest()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()  # type: ignore[call-arg]
