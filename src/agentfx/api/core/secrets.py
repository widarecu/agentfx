"""At-rest encryption for user-owned secrets (US-019).

Wraps `cryptography.fernet.Fernet` — symmetric AES-128-CBC + HMAC-SHA256
authenticated encryption — so the plaintext of a user's OANDA API key
never sits on disk. The key comes from `settings.FERNET_KEY`; missing
in persistent environments (staging / production / UAT) fails at
startup via the config validator, so a mis-provisioned instance can
never quietly write unencrypted secrets.

Design constraints:

- **Fail closed.** Empty / malformed keys raise at first-use rather than
  silently producing garbage ciphertext.
- **Never log plaintext.** Callers pass strings; the wrapper never
  echoes the input, never emits stack traces containing the argument.
- **Ephemeral dev/test key.** If `FERNET_KEY` is unset in a
  development or test environment, we lazily mint a per-process key so
  a fresh clone works without env fiddling. That key does not persist —
  restarting the process invalidates all ciphertext produced under it.
  In production/staging/UAT the config validator refuses to boot with
  an empty key, so this fallback never applies there.

The module deliberately exposes only three functions. Callers do not
touch `Fernet` instances directly.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class SecretEncryptionError(Exception):
    """Raised for any encryption / decryption failure.

    The message deliberately omits the ciphertext / plaintext that
    triggered the failure so a stray log line can't leak a partial
    secret.
    """


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Build the process-wide `Fernet` instance from settings.

    Cached so the constructor's base64 decode + HMAC key derivation
    only happens once per process. If `FERNET_KEY` is unset in a
    development / test environment we mint an ephemeral key; a
    warning goes to the log so it's obvious in a `docker logs` sweep.
    """
    # Imported inside the function so `import secrets` at module load
    # doesn't force the Pydantic settings graph before other tests
    # have set env vars.
    from .config import settings

    key = settings.FERNET_KEY
    if not key:
        # Config's field_validator already refuses an empty key in
        # production/staging/UAT — this branch only ever fires in
        # local dev or the test suite when a developer hasn't set
        # FERNET_KEY. Minting a per-process ephemeral key means
        # `make test-unit` works out of the box without polluting
        # committed env files.
        logger.warning(
            "FERNET_KEY not set — using an ephemeral per-process key. "
            "This is safe for dev/test only; persistent environments "
            "must set FERNET_KEY explicitly."
        )
        key = Fernet.generate_key().decode()
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        raise SecretEncryptionError(
            "FERNET_KEY is not a valid Fernet key. "
            "Generate a fresh one with: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from e


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt `plaintext` and return the ciphertext as raw bytes.

    The Fernet token itself is base64-url-safe, but we return bytes so
    the DB column can be `BYTEA` — no encoding round-trip on read, no
    accidental logging of a str that looks readable but is actually
    ciphertext.
    """
    if not isinstance(plaintext, str):
        raise SecretEncryptionError("encrypt_secret requires a string plaintext")
    if plaintext == "":
        raise SecretEncryptionError("encrypt_secret: empty plaintext is not allowed")
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt `ciphertext` and return the plaintext as a string.

    Raises `SecretEncryptionError` on tamper / wrong-key / malformed
    input. The exception message never contains the ciphertext or
    partial decryption — callers can safely log the exception.
    """
    if not isinstance(ciphertext, bytes):
        raise SecretEncryptionError("decrypt_secret requires bytes ciphertext")
    try:
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as e:
        # Do NOT include the ciphertext in the message.
        raise SecretEncryptionError(
            "Failed to decrypt secret: invalid token or wrong key"
        ) from e
    except UnicodeDecodeError as e:  # pragma: no cover — implies corrupted state
        raise SecretEncryptionError(
            "Failed to decrypt secret: plaintext is not valid UTF-8"
        ) from e


def reset_fernet_cache_for_tests() -> None:
    """Clear the `_fernet` cache so a test can swap `FERNET_KEY`.

    Only used by test helpers. Not part of the runtime API — production
    code should never call this.
    """
    _fernet.cache_clear()


__all__ = [
    "SecretEncryptionError",
    "decrypt_secret",
    "encrypt_secret",
    "reset_fernet_cache_for_tests",
]
