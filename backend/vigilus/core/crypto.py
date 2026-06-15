"""Fernet-based encryption for secrets stored in the database."""

from __future__ import annotations

from cryptography.fernet import Fernet

from vigilus.config import get_settings


def _get_fernet() -> Fernet:
    """Build a Fernet instance from the application secret key."""
    return Fernet(get_settings().fernet_key)


def encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* and return a URL-safe base64-encoded ciphertext string."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet *ciphertext* string back to plaintext."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
