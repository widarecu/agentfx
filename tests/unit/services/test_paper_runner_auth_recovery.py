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
