"""Schema-level tests for the v0.8.1 C.3 OANDA account pool model.

Mirrors `test_user_credentials_models.py` — in-memory SQLite, no
Alembic. Verifies:

- Ciphertext round-trips through the ORM.
- `__repr__` does not leak the ciphertext or secret-looking data.
- `account_id` is unique.
- Default status is FREE on insert; enums round-trip as their string
  values (needed by the admin router's `.value` extraction).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from agentfx.api.core.database import Base
from agentfx.api.core.secrets import decrypt_secret, encrypt_secret
from agentfx.api.models import (
    OandaAccountPool,
    OandaAccountPoolEnvironment,
    OandaAccountPoolStatus,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db(engine):
    with Session(engine) as sess:
        yield sess


class TestPoolRow:
    def test_persist_and_reload(self, db):
        ct = encrypt_secret("oanda-pool-token-xyz")
        row = OandaAccountPool(
            account_id="101-004-12345678-001",
            api_key_encrypted=ct,
            environment=OandaAccountPoolEnvironment.PRACTICE,
            nickname="pool-01",
        )
        db.add(row)
        db.commit()

        fetched = db.scalars(
            select(OandaAccountPool).where(
                OandaAccountPool.account_id == "101-004-12345678-001"
            )
        ).one()
        assert fetched.api_key_encrypted == ct
        assert decrypt_secret(fetched.api_key_encrypted) == "oanda-pool-token-xyz"
        assert fetched.environment == OandaAccountPoolEnvironment.PRACTICE
        assert fetched.status == OandaAccountPoolStatus.FREE
        assert fetched.key_version == 1
        assert fetched.cumulative_pnl_14d == 0.0
        assert fetched.current_run_id is None
        assert fetched.nickname == "pool-01"

    def test_repr_hides_ciphertext(self, db):
        row = OandaAccountPool(
            account_id="101-004-aaaaaaaa-001",
            api_key_encrypted=encrypt_secret("super-secret-token-literal"),
            environment=OandaAccountPoolEnvironment.PRACTICE,
        )
        db.add(row)
        db.commit()
        r = repr(row)
        assert "super-secret-token-literal" not in r
        # The ciphertext bytes shouldn't leak either.
        assert "gAAAAA" not in r

    def test_account_id_unique(self, db):
        db.add(
            OandaAccountPool(
                account_id="101-004-same-001",
                api_key_encrypted=encrypt_secret("k1"),
                environment=OandaAccountPoolEnvironment.PRACTICE,
            )
        )
        db.commit()
        db.add(
            OandaAccountPool(
                account_id="101-004-same-001",
                api_key_encrypted=encrypt_secret("k2"),
                environment=OandaAccountPoolEnvironment.PRACTICE,
            )
        )
        with pytest.raises(Exception):
            db.commit()

    def test_default_status_is_free(self, db):
        row = OandaAccountPool(
            account_id="101-004-default-001",
            api_key_encrypted=encrypt_secret("k"),
            environment=OandaAccountPoolEnvironment.PRACTICE,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.status == OandaAccountPoolStatus.FREE

    def test_enum_values_round_trip_as_strings(self, db):
        """The admin router + pool_summary rely on `.value` extraction;
        verify the enum values are the lower-case strings we expect."""
        row = OandaAccountPool(
            account_id="101-004-enum-001",
            api_key_encrypted=encrypt_secret("k"),
            environment=OandaAccountPoolEnvironment.LIVE,
            status=OandaAccountPoolStatus.COOLING_DOWN,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.environment.value == "live"
        assert row.status.value == "cooling_down"
