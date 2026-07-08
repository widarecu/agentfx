"""Schema-level tests for the v0.6.0 user OANDA credentials models.

Uses in-memory SQLite (matching test_evolution_models.py). The
Alembic migration is Postgres-specific — a separate integration test
exercises it against the real test stack.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from agentfx.api.core.database import Base
from agentfx.api.core.secrets import decrypt_secret, encrypt_secret
from agentfx.api.models import (
    OandaEnvironment,
    User,
    UserOandaAccount,
    UserOandaCredentials,
    UserRole,
)


@pytest.fixture
def engine():
    """In-memory SQLite with foreign keys enabled."""
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db(engine):
    with Session(engine) as sess:
        yield sess


@pytest.fixture
def alice(db):
    u = User(
        email="alice@example.com",
        hashed_password="hash",
        role=UserRole.TRADER,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


class TestCredentialsRow:
    def test_persist_and_reload(self, db, alice):
        ct = encrypt_secret("api-token-abc")
        row = UserOandaCredentials(
            user_id=alice.id,
            oanda_api_key_encrypted=ct,
            oanda_environment=OandaEnvironment.PRACTICE,
        )
        db.add(row)
        db.commit()

        fetched = db.scalars(
            select(UserOandaCredentials).where(UserOandaCredentials.user_id == alice.id)
        ).one()
        # The ciphertext round-trips byte-for-byte through the ORM.
        assert fetched.oanda_api_key_encrypted == ct
        assert decrypt_secret(fetched.oanda_api_key_encrypted) == "api-token-abc"
        assert fetched.oanda_environment == OandaEnvironment.PRACTICE
        assert fetched.key_version == 1
        assert fetched.verified_at is None

    def test_repr_hides_secret(self, db, alice):
        row = UserOandaCredentials(
            user_id=alice.id,
            oanda_api_key_encrypted=encrypt_secret("super-secret-token"),
            oanda_environment=OandaEnvironment.PRACTICE,
        )
        r = repr(row)
        assert "super-secret-token" not in r
        assert "hash" not in r  # user's password hash also stays out
        assert alice.email not in r

    def test_unique_per_user(self, db, alice):
        db.add(
            UserOandaCredentials(
                user_id=alice.id,
                oanda_api_key_encrypted=encrypt_secret("k1"),
                oanda_environment=OandaEnvironment.PRACTICE,
            )
        )
        db.commit()

        db.add(
            UserOandaCredentials(
                user_id=alice.id,
                oanda_api_key_encrypted=encrypt_secret("k2"),
                oanda_environment=OandaEnvironment.PRACTICE,
            )
        )
        with pytest.raises(Exception) as exc:
            db.commit()
        assert (
            "unique" in str(exc.value).lower()
            or "unique" in str(exc.value.orig).lower()
            if hasattr(exc.value, "orig")
            else True
        )

    def test_cascade_from_user_delete(self, db, alice):
        db.add(
            UserOandaCredentials(
                user_id=alice.id,
                oanda_api_key_encrypted=encrypt_secret("k"),
                oanda_environment=OandaEnvironment.PRACTICE,
            )
        )
        db.commit()
        db.delete(alice)
        db.commit()

        remaining = db.scalars(select(UserOandaCredentials)).all()
        assert remaining == []


class TestAccountsRow:
    def test_persist_with_credentials(self, db, alice):
        creds = UserOandaCredentials(
            user_id=alice.id,
            oanda_api_key_encrypted=encrypt_secret("k"),
            oanda_environment=OandaEnvironment.PRACTICE,
        )
        db.add(creds)
        db.commit()
        db.refresh(creds)

        db.add(
            UserOandaAccount(
                user_credentials_id=creds.id,
                oanda_account_id="101-004-12345678-001",
                alias="Primary",
                currency="USD",
            )
        )
        db.add(
            UserOandaAccount(
                user_credentials_id=creds.id,
                oanda_account_id="101-004-12345678-002",
                alias="Aggressive",
                currency="USD",
            )
        )
        db.commit()

        db.refresh(creds)
        assert len(creds.accounts) == 2
        account_ids = {a.oanda_account_id for a in creds.accounts}
        assert "101-004-12345678-001" in account_ids
        assert "101-004-12345678-002" in account_ids

    def test_account_uniqueness_per_credentials(self, db, alice):
        creds = UserOandaCredentials(
            user_id=alice.id,
            oanda_api_key_encrypted=encrypt_secret("k"),
            oanda_environment=OandaEnvironment.PRACTICE,
        )
        db.add(creds)
        db.commit()
        db.refresh(creds)

        db.add(
            UserOandaAccount(
                user_credentials_id=creds.id,
                oanda_account_id="dup-id",
                currency="USD",
            )
        )
        db.commit()

        db.add(
            UserOandaAccount(
                user_credentials_id=creds.id,
                oanda_account_id="dup-id",
                currency="EUR",
            )
        )
        with pytest.raises(Exception) as exc:
            db.commit()
        msg = str(exc.value).lower()
        assert "unique" in msg or "duplicate" in msg or "constraint" in msg

    def test_cascade_from_credentials_delete(self, db, alice):
        creds = UserOandaCredentials(
            user_id=alice.id,
            oanda_api_key_encrypted=encrypt_secret("k"),
            oanda_environment=OandaEnvironment.PRACTICE,
        )
        db.add(creds)
        db.commit()
        db.refresh(creds)

        db.add(
            UserOandaAccount(
                user_credentials_id=creds.id,
                oanda_account_id="a1",
                currency="USD",
            )
        )
        db.commit()

        db.delete(creds)
        db.commit()

        assert db.scalars(select(UserOandaAccount)).all() == []
