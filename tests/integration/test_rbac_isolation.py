"""Cross-user isolation tests for evolution + optimization endpoints (US-018).

Locks the security invariant "user A cannot see, mutate, or infer the
existence of user B's rows" against the FastAPI TestClient + a real
Postgres. Ships as the regression harness for the security bug the
plan called out: prior to v0.6.0 `list_runs()` returned every user's
rows regardless of `created_by`, and `get_run(id)` had no ownership
gate.

Every test wires two independent TRADER identities plus an ADMIN and
walks the surface: list, read, delete, sub-resources, scope toggle.
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
    if resp.status_code != 200:  # pragma: no cover — test env broken
        pytest.skip(f"Auth failed for {email}: {resp.json()}")
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def rbac_users(db_session: Session):
    """Two independent TRADER users + one ADMIN.

    Uses distinct emails from the seed test_users so runs live under
    a clean created_by regardless of what previous tests left behind.
    """
    from src.agentfx.api.core.security import get_password_hash
    from src.agentfx.api.models.user import User

    creds = {
        "trader_a": ("rbac_trader_a@example.com", "traderApass"),
        "trader_b": ("rbac_trader_b@example.com", "traderBpass"),
        "admin": ("rbac_admin@example.com", "adminpass"),
    }
    roles = {"trader_a": "trader", "trader_b": "trader", "admin": "admin"}

    users = {}
    for key, (email, password) in creds.items():
        user = db_session.query(User).filter_by(email=email).first()
        if user is None:
            user = User(
                email=email,
                hashed_password=get_password_hash(password),
                role=roles[key],
                is_active=True,
            )
            db_session.add(user)
        users[key] = {"email": email, "password": password, "user": user}

    db_session.commit()
    for v in users.values():
        db_session.refresh(v["user"])
    return users


@pytest.fixture
def seed_evolution_runs(db_session: Session, rbac_users) -> dict[str, str]:
    """Persist one evolution run per user and return their IDs."""
    from src.agentfx.api.models.evolution import EvolutionRun, EvolutionRunStatus

    ids = {}
    for key in ("trader_a", "trader_b", "admin"):
        run = EvolutionRun(
            id=_mk_id("evorun"),
            status=EvolutionRunStatus.COMPLETED,
            instrument="EUR_USD",
            granularity="H1",
            config_json={"data_years": 1},
            stop_conditions_json={"max_generations": 1},
            current_generation=1,
            llm_cost_usd=0,
            created_by=rbac_users[key]["user"].id,
        )
        db_session.add(run)
        ids[key] = run.id
    db_session.commit()
    return ids


@pytest.fixture
def seed_optimization_jobs(db_session: Session, rbac_users) -> dict[str, str]:
    from src.agentfx.api.models.optimization import (
        JobStatus,
        OptimizationJob,
        StrategyType,
    )

    ids = {}
    for key in ("trader_a", "trader_b", "admin"):
        job = OptimizationJob(
            id=_mk_id("optjob"),
            strategy=StrategyType.MA,
            pair="EUR_USD",
            status=JobStatus.COMPLETED,
            estimated_iterations=1,
            completed_iterations=1,
            param_ranges={"fast": {"min": 5, "max": 20, "step": 1}},
            created_by=rbac_users[key]["user"].id,
        )
        db_session.add(job)
        ids[key] = job.id
    db_session.commit()
    return ids


# ─────────────────────────────────────────────────────────────────────────
# Evolution — cross-user isolation
# ─────────────────────────────────────────────────────────────────────────


class TestEvolutionRunIsolation:
    def test_list_returns_only_own_runs(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get("/api/v1/evolution-runs", headers=headers)
        assert resp.status_code == 200
        returned_ids = {r["id"] for r in resp.json()["items"]}

        assert seed_evolution_runs["trader_a"] in returned_ids
        assert seed_evolution_runs["trader_b"] not in returned_ids
        assert seed_evolution_runs["admin"] not in returned_ids

    def test_get_others_run_returns_403(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        # Trader A tries to fetch Trader B's run by ID
        resp = test_client_with_db.get(
            f"/api/v1/evolution-runs/{seed_evolution_runs['trader_b']}",
            headers=headers,
        )
        assert resp.status_code == 403
        assert "authorized" in resp.json()["detail"].lower()

    def test_delete_others_run_returns_403(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.delete(
            f"/api/v1/evolution-runs/{seed_evolution_runs['trader_b']}",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_stop_others_run_returns_403(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.post(
            f"/api/v1/evolution-runs/{seed_evolution_runs['trader_b']}/stop",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_generations_of_others_run_returns_403(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get(
            f"/api/v1/evolution-runs/{seed_evolution_runs['trader_b']}/generations",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_decisions_of_others_run_returns_403(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get(
            f"/api/v1/evolution-runs/{seed_evolution_runs['trader_b']}/decisions",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_own_run_still_readable(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get(
            f"/api/v1/evolution-runs/{seed_evolution_runs['trader_a']}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == seed_evolution_runs["trader_a"]


class TestEvolutionAdminScope:
    def test_admin_default_scope_returns_only_own(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["admin"]["email"],
            rbac_users["admin"]["password"],
        )
        resp = test_client_with_db.get("/api/v1/evolution-runs", headers=headers)
        assert resp.status_code == 200
        returned_ids = {r["id"] for r in resp.json()["items"]}
        # Default scope=my — even ADMIN sees only their own by default so
        # a fresh admin session doesn't accidentally expose everyone.
        assert seed_evolution_runs["admin"] in returned_ids
        assert seed_evolution_runs["trader_a"] not in returned_ids
        assert seed_evolution_runs["trader_b"] not in returned_ids

    def test_admin_scope_all_sees_everyone(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["admin"]["email"],
            rbac_users["admin"]["password"],
        )
        resp = test_client_with_db.get(
            "/api/v1/evolution-runs?scope=all&page_size=100", headers=headers
        )
        assert resp.status_code == 200
        returned_ids = {r["id"] for r in resp.json()["items"]}
        assert seed_evolution_runs["trader_a"] in returned_ids
        assert seed_evolution_runs["trader_b"] in returned_ids
        assert seed_evolution_runs["admin"] in returned_ids

    def test_admin_can_read_others_run(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["admin"]["email"],
            rbac_users["admin"]["password"],
        )
        resp = test_client_with_db.get(
            f"/api/v1/evolution-runs/{seed_evolution_runs['trader_a']}",
            headers=headers,
        )
        assert resp.status_code == 200

    def test_trader_scope_all_returns_403(
        self, test_client_with_db, rbac_users, seed_evolution_runs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get(
            "/api/v1/evolution-runs?scope=all", headers=headers
        )
        assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────
# Optimization — cross-user isolation
# ─────────────────────────────────────────────────────────────────────────


class TestOptimizationJobIsolation:
    def test_list_returns_only_own_jobs(
        self, test_client_with_db, rbac_users, seed_optimization_jobs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get("/api/v1/optimization-jobs", headers=headers)
        assert resp.status_code == 200
        returned_ids = {r["id"] for r in resp.json()["items"]}
        assert seed_optimization_jobs["trader_a"] in returned_ids
        assert seed_optimization_jobs["trader_b"] not in returned_ids

    def test_get_others_job_returns_403(
        self, test_client_with_db, rbac_users, seed_optimization_jobs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get(
            f"/api/v1/optimization-jobs/{seed_optimization_jobs['trader_b']}",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_delete_others_job_returns_403(
        self, test_client_with_db, rbac_users, seed_optimization_jobs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.delete(
            f"/api/v1/optimization-jobs/{seed_optimization_jobs['trader_b']}",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_trials_of_others_job_returns_403(
        self, test_client_with_db, rbac_users, seed_optimization_jobs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get(
            f"/api/v1/optimization-jobs/{seed_optimization_jobs['trader_b']}/trials",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_own_job_readable(
        self, test_client_with_db, rbac_users, seed_optimization_jobs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get(
            f"/api/v1/optimization-jobs/{seed_optimization_jobs['trader_a']}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == seed_optimization_jobs["trader_a"]


class TestOptimizationAdminScope:
    def test_admin_scope_all_sees_everyone(
        self, test_client_with_db, rbac_users, seed_optimization_jobs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["admin"]["email"],
            rbac_users["admin"]["password"],
        )
        resp = test_client_with_db.get(
            "/api/v1/optimization-jobs?scope=all&page_size=100", headers=headers
        )
        assert resp.status_code == 200
        returned_ids = {r["id"] for r in resp.json()["items"]}
        assert seed_optimization_jobs["trader_a"] in returned_ids
        assert seed_optimization_jobs["trader_b"] in returned_ids

    def test_trader_scope_all_returns_403(
        self, test_client_with_db, rbac_users, seed_optimization_jobs
    ):
        headers = _auth_headers(
            test_client_with_db,
            rbac_users["trader_a"]["email"],
            rbac_users["trader_a"]["password"],
        )
        resp = test_client_with_db.get(
            "/api/v1/optimization-jobs?scope=all", headers=headers
        )
        assert resp.status_code == 403
