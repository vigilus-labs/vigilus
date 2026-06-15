"""Unit tests for vigilus.core.auth."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest

from vigilus.core.auth import (
    JWT_ALGORITHM,
    LoginRateLimiter,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


# ── Password hashing ──────────────────────────────────────────────────────────

def test_hash_verify_roundtrip():
    h = hash_password("my-password-123")
    assert verify_password("my-password-123", h) is True


def test_wrong_password_returns_false():
    h = hash_password("correct-horse-battery")
    assert verify_password("wrong-password", h) is False


def test_garbage_hash_returns_false():
    assert verify_password("anything", "not-a-real-hash") is False


# ── JWT tokens ────────────────────────────────────────────────────────────────

def test_create_decode_roundtrip():
    token = create_token("user-abc", token_version=3)
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "user-abc"
    assert payload["ver"] == 3


def test_expired_token_returns_none():
    from vigilus.config import get_settings
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user-abc",
        "ver": 0,
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
    }
    token = jwt.encode(payload, settings.jwt_key, algorithm=JWT_ALGORITHM)
    assert decode_token(token) is None


def test_tampered_token_returns_none():
    token = create_token("user-abc", token_version=0)
    # Flip a character in the signature
    tampered = token[:-4] + "XXXX"
    assert decode_token(tampered) is None


def test_wrong_key_returns_none():
    payload = {
        "sub": "user-abc",
        "ver": 0,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = jwt.encode(payload, "wrong-key-entirely", algorithm=JWT_ALGORITHM)
    assert decode_token(token) is None


# ── Rate limiter ──────────────────────────────────────────────────────────────

def test_allows_before_limit():
    limiter = LoginRateLimiter()
    for _ in range(4):
        limiter.record_failure("alice", "1.2.3.4")
    assert limiter.check("alice", "1.2.3.4") is True


def test_locks_after_limit():
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.record_failure("alice", "1.2.3.4")
    assert limiter.check("alice", "1.2.3.4") is False


def test_success_clears_count():
    limiter = LoginRateLimiter()
    for _ in range(4):
        limiter.record_failure("alice", "1.2.3.4")
    limiter.record_success("alice", "1.2.3.4")
    assert limiter.check("alice", "1.2.3.4") is True


def test_lockout_expires():
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.record_failure("alice", "1.2.3.4")
    assert limiter.check("alice", "1.2.3.4") is False

    # Fake the locked_until to be in the past
    key = ("alice", "1.2.3.4")
    limiter._data[key]["locked_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert limiter.check("alice", "1.2.3.4") is True


def test_different_ips_tracked_separately():
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.record_failure("alice", "1.2.3.4")
    # Different IP should not be locked
    assert limiter.check("alice", "9.9.9.9") is True
