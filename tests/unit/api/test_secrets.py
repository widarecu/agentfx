"""Unit tests for src/agentfx/api/core/secrets.py (US-019).

Pure — no DB, no HTTP. Locks the invariants the paper-runner and the
credentials router depend on:

- round-trip identity
- tamper detection surfaces as `SecretEncryptionError` (never as a
  quiet wrong-plaintext return)
- key mismatch surfaces the same way
- rejects non-string / empty input
- config validator forbids empty `FERNET_KEY` in staging / production
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from agentfx.api.core.secrets import (
    SecretEncryptionError,
    decrypt_secret,
    encrypt_secret,
    reset_fernet_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_fernet_between_tests():
    """Clear the Fernet cache before every test so a `FERNET_KEY`
    override in one test cannot bleed into another."""
    reset_fernet_cache_for_tests()
    yield
    reset_fernet_cache_for_tests()


class TestRoundTrip:
    def test_encrypt_then_decrypt_recovers_plaintext(self):
        ct = encrypt_secret("hunter2")
        assert isinstance(ct, bytes)
        assert decrypt_secret(ct) == "hunter2"

    def test_encrypt_is_non_deterministic(self):
        # Fernet uses a random IV — the same plaintext must produce
        # different ciphertexts on each call.
        a = encrypt_secret("same-key")
        b = encrypt_secret("same-key")
        assert a != b
        assert decrypt_secret(a) == decrypt_secret(b) == "same-key"

    def test_handles_unicode(self):
        secret = "🔑 ★ β γ  αβγ"
        assert decrypt_secret(encrypt_secret(secret)) == secret


class TestFailureModes:
    def test_rejects_non_string_plaintext(self):
        with pytest.raises(SecretEncryptionError):
            encrypt_secret(b"raw-bytes")  # type: ignore[arg-type]
        with pytest.raises(SecretEncryptionError):
            encrypt_secret(None)  # type: ignore[arg-type]
        with pytest.raises(SecretEncryptionError):
            encrypt_secret(42)  # type: ignore[arg-type]

    def test_rejects_empty_plaintext(self):
        with pytest.raises(SecretEncryptionError):
            encrypt_secret("")

    def test_decrypt_rejects_non_bytes(self):
        with pytest.raises(SecretEncryptionError):
            decrypt_secret("not-bytes")  # type: ignore[arg-type]

    def test_decrypt_rejects_tampered_ciphertext(self):
        ct = bytearray(encrypt_secret("hunter2"))
        # Flip one byte anywhere past the version prefix so we hit HMAC.
        ct[-1] ^= 0x01
        with pytest.raises(SecretEncryptionError):
            decrypt_secret(bytes(ct))

    def test_decrypt_rejects_garbage(self):
        with pytest.raises(SecretEncryptionError):
            decrypt_secret(b"not-a-fernet-token")

    def test_decrypt_rejects_ciphertext_from_wrong_key(self):
        ct = encrypt_secret("hunter2")

        # Swap in a totally unrelated key by resetting the settings
        # cache and pointing `_fernet` at a different key.
        reset_fernet_cache_for_tests()
        other_key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"FERNET_KEY": other_key}, clear=False):
            with patch("agentfx.api.core.secrets._fernet") as mock:
                mock.return_value = Fernet(other_key.encode())
                # Direct decrypt should fail — this ciphertext was
                # produced under a different key.
                with pytest.raises(SecretEncryptionError):
                    decrypt_secret(ct)


class TestNoCiphertextInErrorMessages:
    """The message on `SecretEncryptionError` MUST NOT contain any part
    of the ciphertext — otherwise a stray log line leaks material that
    is directly useful to an offline attacker.
    """

    def test_decrypt_tamper_error_omits_ciphertext(self):
        ct = bytearray(encrypt_secret("s3cret-value-do-not-log"))
        ct[-1] ^= 0x01
        tampered = bytes(ct)
        with pytest.raises(SecretEncryptionError) as exc:
            decrypt_secret(tampered)
        msg = str(exc.value)
        assert tampered.hex() not in msg
        # Also refuse: a snippet of the base64-ish encoded form. Fernet
        # tokens are urlsafe-base64, so the first 20 chars form a
        # distinctive prefix; if any of it leaks that's a bug.
        assert tampered[:20] not in msg.encode()


class TestFernetKeyValidator:
    """The Pydantic validator on `Settings.FERNET_KEY` refuses an empty
    value in staging / production / UAT. Dev + test may leave it blank
    (the module mints an ephemeral key)."""

    @pytest.mark.parametrize("env", ["production", "staging", "uat"])
    def test_empty_key_rejected_in_persistent_env(self, env):
        # Fresh import so the validator runs against our patched env.
        from importlib import reload

        with patch.dict(
            os.environ, {"ENVIRONMENT": env, "FERNET_KEY": ""}, clear=False
        ):
            from agentfx.api.core import config as config_module

            with pytest.raises(Exception) as exc:
                reload(config_module)
            # Pydantic wraps validator failures in a ValidationError
            assert "FERNET_KEY" in str(exc.value)

    def test_empty_key_permitted_in_development(self):
        from importlib import reload

        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "development", "FERNET_KEY": ""},
            clear=False,
        ):
            from agentfx.api.core import config as config_module

            reload(config_module)  # should NOT raise
            assert config_module.settings.FERNET_KEY == ""
