"""Password hashing and JWT session tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from vigilus.config import get_settings

_hasher = PasswordHasher()

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # malformed hash etc. — treat as failure, never raise to caller
        return False


# Pre-computed hash used to equalize timing when the username doesn't exist.
_DUMMY_HASH = _hasher.hash("vigilus-dummy-password-for-timing")


def dummy_verify() -> None:
    """Burn the same time as a real verify, for unknown usernames."""
    try:
        _hasher.verify(_DUMMY_HASH, "not-the-password")
    except VerifyMismatchError:
        pass


def create_token(user_id: str, token_version: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(hours=settings.auth_token_ttl_hours),
    }
    return jwt.encode(payload, settings.jwt_key, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """Return the payload, or None for any invalid/expired token."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_key, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return None


# ── Login rate limiter ────────────────────────────────────────────────────────


class LoginRateLimiter:
    """Track failed logins per (username, ip); lock out after N failures."""

    def __init__(self) -> None:
        # key: (username, ip) → {"count": int, "locked_until": datetime | None}
        self._data: dict[tuple[str, str], dict] = {}

    def _key(self, username: str, ip: str) -> tuple[str, str]:
        return (username.lower(), ip)

    def check(self, username: str, ip: str) -> bool:
        """Return False if currently locked out, True if login may proceed."""
        entry = self._data.get(self._key(username, ip))
        if entry is None:
            return True
        locked_until = entry.get("locked_until")
        if locked_until and datetime.now(UTC) < locked_until:
            return False
        return True

    def record_failure(self, username: str, ip: str) -> None:
        settings = get_settings()
        key = self._key(username, ip)
        entry = self._data.setdefault(key, {"count": 0, "locked_until": None})
        # Clear expired lockout before counting
        if entry["locked_until"] and datetime.now(UTC) >= entry["locked_until"]:
            entry["count"] = 0
            entry["locked_until"] = None
        entry["count"] += 1
        if entry["count"] >= settings.auth_max_login_failures:
            entry["locked_until"] = datetime.now(UTC) + timedelta(
                minutes=settings.auth_lockout_minutes
            )

    def record_success(self, username: str, ip: str) -> None:
        self._data.pop(self._key(username, ip), None)


login_limiter = LoginRateLimiter()
