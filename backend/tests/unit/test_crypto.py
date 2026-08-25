"""Unit tests for app.services.crypto.

Covers:
  - Fernet key wiring from env
  - encrypt → decrypt roundtrip
  - Key rotation: old key decrypts existing ciphertext; new key produces fresh
  - Refuses empty plaintext
  - InvalidToken → TokenEncryptionError
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.services import crypto

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _reset_crypto_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test sets its own keys; clear the lru_cache between tests."""
    crypto.reset_cache_for_tests()
    yield
    crypto.reset_cache_for_tests()


class TestEncryptDecrypt:
    def test_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", key)
        ct = crypto.encrypt_token("xoxb-test-1234567890")
        assert ct != "xoxb-test-1234567890"
        assert crypto.decrypt_token(ct) == "xoxb-test-1234567890"

    def test_different_plaintexts_produce_different_ciphertexts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        a = crypto.encrypt_token("token-a")
        b = crypto.encrypt_token("token-b")
        assert a != b

    def test_same_plaintext_encrypts_to_different_ciphertext(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fernet includes an IV; same plaintext yields different ciphertext."""
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        a = crypto.encrypt_token("xoxb-test")
        crypto.reset_cache_for_tests()
        b = crypto.encrypt_token("xoxb-test")
        assert a != b
        assert crypto.decrypt_token(a) == crypto.decrypt_token(b) == "xoxb-test"


class TestKeyMissing:
    def test_no_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
        with pytest.raises(crypto.TokenEncryptionError, match="not set"):
            crypto.encrypt_token("anything")


class TestEmptyPlaintext:
    def test_empty_string_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        with pytest.raises(crypto.TokenEncryptionError, match="empty"):
            crypto.encrypt_token("")


class TestKeyRotation:
    def test_old_key_decrypts_existing_ciphertext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rotate flow: encrypt with K1, then move K1 to *_OLD and K2 to primary."""
        k1 = Fernet.generate_key().decode()
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", k1)
        ct = crypto.encrypt_token("rotate-me")

        # Rotate: new primary, old now secondary
        k2 = Fernet.generate_key().decode()
        crypto.reset_cache_for_tests()
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", k2)
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY_OLD", k1)

        # Old ciphertext still decrypts.
        assert crypto.decrypt_token(ct) == "rotate-me"

        # New encrypt uses K2 — re-rotate ciphertext.
        ct_new = crypto.rotate_ciphertext(ct)
        assert ct_new != ct
        assert crypto.decrypt_token(ct_new) == "rotate-me"

    def test_decrypt_with_no_matching_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        k1 = Fernet.generate_key().decode()
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", k1)
        ct = crypto.encrypt_token("foo")

        # Switch to a fresh key with no overlap.
        crypto.reset_cache_for_tests()
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY_OLD", raising=False)

        with pytest.raises(crypto.TokenEncryptionError, match="does not decrypt"):
            crypto.decrypt_token(ct)


class TestMalformed:
    def test_garbage_ciphertext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        with pytest.raises(crypto.TokenEncryptionError):
            crypto.decrypt_token("not-a-real-ciphertext")

    def test_empty_ciphertext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        with pytest.raises(crypto.TokenEncryptionError, match="non-empty"):
            crypto.decrypt_token("")
