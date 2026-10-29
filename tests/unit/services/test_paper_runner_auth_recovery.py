"""Unit tests for the T0.2 401 auth-recovery path (Wilson UAT 2026-07-15)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentfx.adapters.oanda import OandaError
from agentfx.services.paper_runner import _is_auth_error, _recover_from_auth_loss

pytestmark = pytest.mark.unit


class TestIsAuthError:
    def test_401_is_auth(self):
        assert _is_auth_error(OandaError("401 Insufficient authorization"))

    def test_403_is_auth(self):
        assert _is_auth_error(OandaError("403 Forbidden"))

    def test_400_is_not_auth(self):
        assert not _is_auth_error(OandaError("400 Bad request"))

    def test_5xx_is_not_auth(self):
        assert not _is_auth_error(OandaError("503 Service unavailable"))

    def test_connection_error_is_not_auth(self):
        assert not _is_auth_error(OandaError("connection_error: Connection reset"))


class TestRecoverFromAuthLoss:
    def _run(self, user_id=42):
        return SimpleNamespace(
            id="run_test",
            user_id=user_id,
            oanda_account_id="101-011-99-001",
        )

    def _creds(self):
        return SimpleNamespace(
            oanda_api_key_encrypted=b"cipher",
            oanda_environment=SimpleNamespace(value="practice"),
        )

    def test_no_creds_returns_none(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.one_or_none.return_value = None
        run = self._run()

        with patch("agentfx.api.models.UserOandaCredentials", MagicMock()):
            result = _recover_from_auth_loss(db, run, OandaError("401"))

        assert result is None

    def test_decrypt_failure_returns_none(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.one_or_none.return_value = (
            self._creds()
        )
        run = self._run()

        with (
            patch("agentfx.api.models.UserOandaCredentials", MagicMock()),
            patch(
                "agentfx.api.core.secrets.decrypt_secret",
                side_effect=RuntimeError("bad ciphertext"),
            ),
        ):
            result = _recover_from_auth_loss(db, run, OandaError("401"))

        assert result is None

    def test_fresh_client_still_401_returns_none(self):
        """Cred refresh succeeded (decrypt OK, OandaClient built) but
        the fresh client also 401s → creds are truly bad, not just
        stale in the subprocess."""
        db = MagicMock()
        db.query.return_value.filter.return_value.one_or_none.return_value = (
            self._creds()
        )
        run = self._run()

        fresh_client = MagicMock()
        fresh_client.get_account_balance.side_effect = OandaError(
            "401 Insufficient authorization"
        )

        with (
            patch("agentfx.api.models.UserOandaCredentials", MagicMock()),
            patch(
                "agentfx.api.core.secrets.decrypt_secret",
                return_value="fresh-key",
            ),
            patch(
                "agentfx.adapters.oanda.OandaClient",
                return_value=fresh_client,
            ),
        ):
            result = _recover_from_auth_loss(db, run, OandaError("401"))

        assert result is None

    def test_fresh_client_recovers(self):
        """The common recovery case: either the user re-verified while
        the run was live, OR the long-lived Session was corrupted and
        a fresh one with the same token works."""
        db = MagicMock()
        db.query.return_value.filter.return_value.one_or_none.return_value = (
            self._creds()
        )
        run = self._run()

        fresh_client = MagicMock()
        fresh_client.get_account_balance.return_value = {
            "balance": 10_000.0,
            "currency": "USD",
        }

        with (
            patch("agentfx.api.models.UserOandaCredentials", MagicMock()),
            patch(
                "agentfx.api.core.secrets.decrypt_secret",
                return_value="fresh-key",
            ),
            patch(
                "agentfx.adapters.oanda.OandaClient",
                return_value=fresh_client,
            ),
        ):
            result = _recover_from_auth_loss(db, run, OandaError("401"))

        assert result is fresh_client
        fresh_client.get_account_balance.assert_called_once()

    def test_pool_sourced_run_uses_pool_key_not_user_creds(self):
        """AFX-7: when the run was pool-allocated, auth refresh must
        re-decrypt the pool row's key — not the owner's
        UserOandaCredentials, which belong to a different account."""
        db = MagicMock()
        run = self._run()

        pool_row = SimpleNamespace(
            id="pool-row-abc",
            api_key_encrypted=b"pool-cipher",
            environment=SimpleNamespace(value="practice"),
            account_id="101-pool-1",
        )
        # First `.first()` call returns the pool row; make sure no
        # UserOandaCredentials query ever fires.
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = pool_row

        fresh_client = MagicMock()
        fresh_client.get_account_balance.return_value = {"balance": 10_000.0}

        with (
            patch(
                "agentfx.api.core.secrets.decrypt_secret",
                return_value="pool-plaintext-key",
            ) as decrypt_mock,
            patch(
                "agentfx.adapters.oanda.OandaClient",
                return_value=fresh_client,
            ) as client_cls,
        ):
            result = _recover_from_auth_loss(db, run, OandaError("401"))

        assert result is fresh_client
        decrypt_mock.assert_called_once_with(b"pool-cipher")
        # OandaClient must be instantiated with the POOL's account_id,
        # not run.oanda_account_id (which was the owner default).
        _, kwargs = client_cls.call_args
        assert kwargs["account_id"] == "101-pool-1"
        assert kwargs["api_key"] == "pool-plaintext-key"
        assert kwargs["environment"] == "practice"

    def test_pool_decrypt_failure_returns_none(self):
        """A broken pool entry (Fernet key rotated, ciphertext
        corrupted) must halt the auth refresh — do NOT silently fall
        through to the user's own creds, which would trade on the
        wrong account."""
        db = MagicMock()
        run = self._run()

        pool_row = SimpleNamespace(
            id="pool-row-x",
            api_key_encrypted=b"bad-cipher",
            environment=SimpleNamespace(value="practice"),
            account_id="101-pool-1",
        )
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = pool_row

        with patch(
            "agentfx.api.core.secrets.decrypt_secret",
            side_effect=RuntimeError("fernet rotated"),
        ):
            result = _recover_from_auth_loss(db, run, OandaError("401"))
        assert result is None

    def test_pool_key_never_logged(self, caplog):
        """Decrypted API keys must never appear in logs — a regression
        here is a credential-exposure incident."""
        import logging

        db = MagicMock()
        run = self._run()
        pool_row = SimpleNamespace(
            id="pool-row-y",
            api_key_encrypted=b"cipher",
            environment=SimpleNamespace(value="practice"),
            account_id="101-pool-1",
        )
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = pool_row

        fresh_client = MagicMock()
        fresh_client.get_account_balance.return_value = {"balance": 10_000.0}

        secret_token = "ULTRA_SECRET_TOKEN_SHOULD_NEVER_LOG"
        with (
            patch("agentfx.api.core.secrets.decrypt_secret", return_value=secret_token),
            patch("agentfx.adapters.oanda.OandaClient", return_value=fresh_client),
            caplog.at_level(logging.DEBUG),
        ):
            _recover_from_auth_loss(db, run, OandaError("401"))

        for record in caplog.records:
            assert secret_token not in record.getMessage()

    def test_fresh_client_non_auth_error_propagates(self):
        """A 5xx during the recovery health-check is a real problem
        the caller must see — recovery didn't fail auth-wise, so we
        should not silently return None (that would falsely halt with
        oanda_auth_lost)."""
        db = MagicMock()
        db.query.return_value.filter.return_value.one_or_none.return_value = (
            self._creds()
        )
        run = self._run()

        fresh_client = MagicMock()
        fresh_client.get_account_balance.side_effect = OandaError(
            "503 Service unavailable"
        )

        with (
            patch("agentfx.api.models.UserOandaCredentials", MagicMock()),
            patch(
                "agentfx.api.core.secrets.decrypt_secret",
                return_value="fresh-key",
            ),
            patch(
                "agentfx.adapters.oanda.OandaClient",
                return_value=fresh_client,
            ),
            pytest.raises(OandaError, match="503"),
        ):
            _recover_from_auth_loss(db, run, OandaError("401"))
