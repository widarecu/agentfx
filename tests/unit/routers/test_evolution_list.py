"""Unit tests for GET /api/v1/evolution-runs: sorting + outcome columns (AFX-37).

The portal's Evolution runs table sorts server-side via `sort_by` /
`sort_order` and renders `validator_passed_count` + `best_fitness` per
row. These tests pin that HTTP contract (param names, defaults,
validation, response fields) with the service layer mocked. The SQL
behaviour itself is covered in tests/unit/services/test_evolution_service.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentfx.api import models
from agentfx.api.core.database import get_db
from agentfx.api.routers import evolution as evolution_router
from agentfx.api.services import evolution as evolution_service
from agentfx.api.services.user import get_current_active_user

LIST_RUNS = "agentfx.api.routers.evolution.evolution_service.list_runs"


@pytest.fixture
def trader_user():
    user = Mock(spec=models.User)
    user.id = 7
    user.email = "trader@example.com"
    user.role = models.UserRole.TRADER
    return user


@pytest.fixture
def client(trader_user):
    app = FastAPI()
    app.include_router(evolution_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: Mock()
    app.dependency_overrides[get_current_active_user] = lambda: trader_user
    return TestClient(app)


def _row(
    run_id: str = "evorun_20261027_dcef6ac7",
    *,
    validator_passed_count: int = 1,
    best_fitness: float | None = 1.264,
) -> evolution_service.RunListRow:
    run = models.EvolutionRun(
        id=run_id,
        status=models.EvolutionRunStatus.COMPLETED,
        instrument="AUD_USD",
        granularity="M30",
        config_json={},
        stop_conditions_json={},
        stop_reason="max_generations",
        current_generation=29,
        llm_cost_usd=Decimal("0.1234"),
        created_at=datetime(2026, 10, 27, 14, 0, tzinfo=UTC),
    )
    return evolution_service.RunListRow(
        run=run,
        validator_passed_count=validator_passed_count,
        best_fitness=best_fitness,
    )


def test_defaults_to_newest_first(client):
    with patch(LIST_RUNS, return_value=([], 0)) as list_runs:
        resp = client.get("/api/v1/evolution-runs")

    assert resp.status_code == 200
    kwargs = list_runs.call_args.kwargs
    assert kwargs["sort_by"] == "created_at"
    assert kwargs["sort_order"] == "desc"


@pytest.mark.parametrize(
    "sort_by", ["created_at", "validator_passed_count", "best_fitness"]
)
@pytest.mark.parametrize("sort_order", ["asc", "desc"])
def test_forwards_sort_params_to_service(client, trader_user, sort_by, sort_order):
    with patch(LIST_RUNS, return_value=([], 0)) as list_runs:
        resp = client.get(
            "/api/v1/evolution-runs",
            params={"sort_by": sort_by, "sort_order": sort_order, "page": 2},
        )

    assert resp.status_code == 200
    kwargs = list_runs.call_args.kwargs
    assert kwargs["sort_by"] == sort_by
    assert kwargs["sort_order"] == sort_order
    # Sorting must not loosen pagination or the US-018 ownership filter.
    assert kwargs["skip"] == 10
    assert kwargs["limit"] == 10
    assert kwargs["created_by"] == trader_user.id


@pytest.mark.parametrize(
    "params",
    [
        {"sort_by": "fitness"},
        {"sort_by": "created_at; DROP TABLE evolution_runs"},
        {"sort_order": "descending"},
    ],
)
def test_rejects_unknown_sort_values_with_422(client, params):
    with patch(LIST_RUNS, return_value=([], 0)) as list_runs:
        resp = client.get("/api/v1/evolution-runs", params=params)

    assert resp.status_code == 422
    list_runs.assert_not_called()


def test_items_carry_validator_passed_count_and_best_fitness(client):
    rows = [
        _row(),
        _row("evorun_20261027_queued", validator_passed_count=0, best_fitness=None),
    ]
    with patch(LIST_RUNS, return_value=(rows, 2)):
        resp = client.get(
            "/api/v1/evolution-runs",
            params={"sort_by": "validator_passed_count"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    winner, queued = body["items"]
    assert winner["id"] == "evorun_20261027_dcef6ac7"
    assert winner["instrument"] == "AUD_USD"
    assert winner["granularity"] == "M30"
    assert winner["validator_passed_count"] == 1
    assert winner["best_fitness"] == pytest.approx(1.264)
    # Existing run fields are still present.
    assert winner["status"] == "completed"
    assert winner["current_generation"] == 29
    assert queued["validator_passed_count"] == 0
    assert queued["best_fitness"] is None


def test_detail_endpoint_shape_is_unchanged(client, trader_user):
    """The outcome columns are list-only. The detail view must not grow
    fields with placeholder values (e.g. a misleading `0` passes)."""
    run = _row().run
    run.created_by = trader_user.id
    with patch(
        "agentfx.api.routers.evolution.evolution_service.get_run", return_value=run
    ):
        resp = client.get(f"/api/v1/evolution-runs/{run.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert "validator_passed_count" not in body
    assert "best_fitness" not in body
