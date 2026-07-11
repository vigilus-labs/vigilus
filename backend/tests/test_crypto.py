"""Tests for the Fernet crypto module."""

from __future__ import annotations

import os

# Ensure env is set before any vigilus imports
os.environ.setdefault("VIGILUS_SECRET", "test-secret-key-for-vigilus-testing-1234")


from vigilus.core.crypto import decrypt, encrypt


class TestCryptoRoundtrip:
    """Verify encrypt/decrypt round-trip behaviour."""

    def test_roundtrip_simple(self):
        """Encrypting then decrypting should return the original plaintext."""
        plaintext = "hello world"
        ciphertext = encrypt(plaintext)
        assert decrypt(ciphertext) == plaintext

    def test_roundtrip_unicode(self):
        """Unicode strings should survive the round-trip."""
        plaintext = "こんにちは世界 🌍"
        ciphertext = encrypt(plaintext)
        assert decrypt(ciphertext) == plaintext

    def test_roundtrip_empty_string(self):
        """Empty strings should round-trip correctly."""
        plaintext = ""
        ciphertext = encrypt(plaintext)
        assert decrypt(ciphertext) == plaintext

    def test_encrypted_differs_from_plaintext(self):
        """The encrypted value should not be the same as the plaintext."""
        plaintext = "super secret data"
        ciphertext = encrypt(plaintext)
        assert ciphertext != plaintext

    def test_different_plaintexts_different_ciphertexts(self):
        """Different inputs should produce different encrypted outputs."""
        ct1 = encrypt("password_one")
        ct2 = encrypt("password_two")
        assert ct1 != ct2

    def test_same_plaintext_different_ciphertexts(self):
        """Fernet produces unique ciphertexts for the same plaintext (due to IV)."""
        plaintext = "determinism test"
        ct1 = encrypt(plaintext)
        ct2 = encrypt(plaintext)
        # Fernet uses a random IV, so even the same plaintext produces different ciphertexts
        assert ct1 != ct2
        # But both should decrypt to the same value
        assert decrypt(ct1) == plaintext
        assert decrypt(ct2) == plaintext
