"""Integration tests for POST /strategy-genomes/{id}/override-validator (US-027).

Locks:
- ADMIN successfully flips the verdict AND a `USER_VALIDATOR_OVERRIDE`
  DecisionLog row lands with the previous / new / reason payload.
- TRADER and VIEWER get 403.
- Reason < 20 chars → 422 (Pydantic Field constraint).
- Unknown genome id → 404.

Reuses the same TestClient + fresh-user pattern as `test_paper_router.py`.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _mk_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{str(uuid4())[:8]}"


def _auth_headers(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/v1/login", data={"username": email, "password": password})
    if resp.status_code != 200:  # pragma: no cover
        pytest.skip(f"Auth failed for {email}: {resp.json()}")
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def override_users(db_session: Session):
    """One ADMIN, one TRADER, one VIEWER — fresh emails per invocation."""
    from src.agentfx.api.core.security import get_password_hash
    from src.agentfx.api.models.user import User

    unique = str(uuid4())[:8]
    creds = {
        "admin": (f"override_admin_{unique}@example.com", "adminpass"),
        "trader": (f"override_trader_{unique}@example.com", "traderpass"),
        "viewer": (f"override_viewer_{unique}@example.com", "viewerpass"),
    }
    roles = {"admin": "admin", "trader": "trader", "viewer": "viewer"}
    users = {}
    for key, (email, password) in creds.items():
        u = User(
            email=email,
            hashed_password=get_password_hash(password),
            role=roles[key],
            is_active=True,
        )
        db_session.add(u)
        users[key] = {"email": email, "password": password, "user": u}
    db_session.commit()
    for v in users.values():
        db_session.refresh(v["user"])
    return users


@pytest.fixture
def failed_genome(db_session: Session, override_users):
    """A genome the validator wrote `false` for — this is the row the
    ADMIN override typically flips true so it can be deployed."""
    from src.agentfx.api.models import (
        EvolutionRun,
        EvolutionRunStatus,
        Generation,
        GenomeOrigin,
        StrategyGenome,
    )

    evo_id = _mk_id("evorun")
    gen_id = f"gen_{evo_id}_1"
    genome_id = f"genome_{gen_id}_0"

    evo = EvolutionRun(
        id=evo_id,
        status=EvolutionRunStatus.COMPLETED,
        instrument="EUR_USD",
        granularity="M1",
        config_json={},
        stop_conditions_json={},
        current_generation=1,
        llm_cost_usd=0,
        created_by=override_users["admin"]["user"].id,
    )
    gen = Generation(id=gen_id, run_id=evo_id, index=1)
    genome = StrategyGenome(
        id=genome_id,
        generation_id=gen_id,
        dsl_json={"stub": True},
        parent_ids=[],
        origin=GenomeOrigin.SEED,
        validator_passed=False,
        validation_failures=["Sharpe 0.34 < 1.0"],
        fitness=0.8,
        metrics_json={"sharpe": 2.6},
    )
    db_session.add_all([evo, gen, genome])
    db_session.commit()
    return {"evo": evo, "gen": gen, "genome": genome}


class TestValidatorOverride:
    def test_admin_flip_persists_and_writes_audit_log(
        self, test_client_with_db, db_session, override_users, failed_genome
    ):
        from src.agentfx.api.models import DecisionLog

        headers = _auth_headers(
            test_client_with_db,
            override_users["admin"]["email"],
            override_users["admin"]["password"],
        )
        genome_id = failed_genome["genome"].id
        resp = test_client_with_db.post(
            f"/api/v1/strategy-genomes/{genome_id}/override-validator",
            headers=headers,
            json={
                "new_value": True,
                "reason": "Unblocking paper trading UI walkthrough demo.",
            },
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["validator_passed"] is True

        # DB row updated.
        db_session.expire_all()
        genome = failed_genome["genome"]
        db_session.refresh(genome)
        assert genome.validator_passed is True

        # DecisionLog row written with previous/new/reason. The
        # `outputs_json` column is JSON (not JSONB) so we filter in
        # Python after fetching by action + user — cheaper than a full
        # scan on a real DB and portable across JSON/JSONB backends.
        override_logs = (
            db_session.query(DecisionLog)
            .filter(DecisionLog.action == "USER_VALIDATOR_OVERRIDE")
            .filter(DecisionLog.user_id == override_users["admin"]["user"].id)
            .all()
        )
        matching = [
            row
            for row in override_logs
            if (row.outputs_json or {}).get("genome_id") == genome_id
        ]
        assert len(matching) == 1
        log = matching[0]
        assert log.outputs_json["previous_value"] is False
        assert log.outputs_json["new_value"] is True
        assert (
            log.outputs_json["reason"]
            == "Unblocking paper trading UI walkthrough demo."
        )
        assert log.agent_type == "USER"

    def test_trader_returns_403(
        self, test_client_with_db, override_users, failed_genome
    ):
        headers = _auth_headers(
            test_client_with_db,
            override_users["trader"]["email"],
            override_users["trader"]["password"],
        )
        resp = test_client_with_db.post(
            f"/api/v1/strategy-genomes/{failed_genome['genome'].id}/override-validator",
            headers=headers,
            json={
                "new_value": True,
                "reason": "trader shouldn't be able to do this at all.",
            },
        )
        assert resp.status_code == 403

    def test_viewer_returns_403(
        self, test_client_with_db, override_users, failed_genome
    ):
        headers = _auth_headers(
            test_client_with_db,
            override_users["viewer"]["email"],
            override_users["viewer"]["password"],
        )
        resp = test_client_with_db.post(
            f"/api/v1/strategy-genomes/{failed_genome['genome'].id}/override-validator",
            headers=headers,
            json={
                "new_value": True,
                "reason": "viewer shouldn't be able to do this at all.",
            },
        )
        assert resp.status_code == 403

    def test_short_reason_returns_422(
        self, test_client_with_db, override_users, failed_genome
    ):
        headers = _auth_headers(
            test_client_with_db,
            override_users["admin"]["email"],
            override_users["admin"]["password"],
        )
        resp = test_client_with_db.post(
            f"/api/v1/strategy-genomes/{failed_genome['genome'].id}/override-validator",
            headers=headers,
            json={"new_value": True, "reason": "too short"},
        )
        assert resp.status_code == 422

    def test_unknown_genome_returns_404(self, test_client_with_db, override_users):
        headers = _auth_headers(
            test_client_with_db,
            override_users["admin"]["email"],
            override_users["admin"]["password"],
        )
        resp = test_client_with_db.post(
            "/api/v1/strategy-genomes/genome_does_not_exist/override-validator",
            headers=headers,
            json={
                "new_value": True,
                "reason": "long enough reason to satisfy the validator.",
            },
        )
        assert resp.status_code == 404
