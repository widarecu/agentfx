"""Unit tests for the paper-run read helpers (US-025 + US-026).

Uses an in-memory SQLite session to exercise the SQLAlchemy queries
against the real ORM. Covers:

- `list_trades_for_run` — pagination + ordering (open trades ahead of
  closed).
- `list_decisions_for_run` — filter by paper_run_id in outputs_json +
  the paper action-type allowlist + `since` cursor.
- `list_equity_for_run` — chronological order + from/to window
  narrowing.
- `get_backtest_overlay` — resolves through the run → genome →
  generation → OOS folds chain and returns the correct `unavailable`
  reason at each missing link.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from agentfx.api import models
from agentfx.api.core.database import Base
from agentfx.api.services import paper as paper_service


pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _enable_fks(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        yield s


def _make_user(db: Session, email: str = "wilson@example.com") -> models.User:
    u = models.User(
        email=email,
        hashed_password="stub",
        role="trader",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_paper_run(db: Session, user_id: int, run_id: str = "run_test") -> models.Run:
    run = models.Run(
        id=run_id,
        strategy_id=None,
        strategy_genome_id=None,
        pair="EUR_USD",
        environment=models.RunEnvironment.PAPER,
        status=models.RunStatus.RUNNING,
        parameters={},
        initial_balance=10_000.0,
        snapshot_balance=10_000.0,
        current_balance=10_000.0,
        pnl=0.0,
        risk_config={},
        user_id=user_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _make_trade(
    db: Session,
    run_id: str,
    *,
    id: str,
    entry_ts: datetime,
    exit_ts: datetime | None = None,
) -> models.Trade:
    trade = models.Trade(
        id=id,
        run_id=run_id,
        entry_timestamp=entry_ts,
        exit_timestamp=exit_ts,
        entry_price=1.10,
        quantity=10_000,
        side="buy",
        origin="live",
        signal_reason="sma > sma",
    )
    db.add(trade)
    db.commit()
    return trade


def _make_decision(
    db: Session,
    *,
    id: str,
    action: str,
    paper_run_id: str | None = None,
    ts: datetime | None = None,
    user_id: int | None = None,
) -> models.DecisionLog:
    outputs = {"paper_run_id": paper_run_id} if paper_run_id is not None else {}
    log = models.DecisionLog(
        id=id,
        run_id=None,
        agent_type="PAPER_RUNNER",
        action=action,
        outputs_json=outputs,
        user_id=user_id,
        created_at=ts,
    )
    db.add(log)
    db.commit()
    return log


# ─────────────────────────────────────────────────────────────────────
# list_trades_for_run
# ─────────────────────────────────────────────────────────────────────


class TestListTradesForRun:
    def test_open_trades_ahead_of_closed(self, db):
        user = _make_user(db)
        run = _make_paper_run(db, user.id)
        base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        closed = _make_trade(
            db, run.id, id="tr_closed", entry_ts=base, exit_ts=base + timedelta(hours=1)
        )
        open_trade = _make_trade(
            db, run.id, id="tr_open", entry_ts=base + timedelta(hours=2)
        )

        items, total = paper_service.list_trades_for_run(db, run.id)

        assert total == 2
        assert items[0].id == open_trade.id  # open trade sorts ahead
        assert items[1].id == closed.id

    def test_pagination(self, db):
        user = _make_user(db)
        run = _make_paper_run(db, user.id)
        base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        for i in range(5):
            _make_trade(db, run.id, id=f"tr_{i}", entry_ts=base + timedelta(minutes=i))

        items, total = paper_service.list_trades_for_run(db, run.id, limit=2, offset=0)
        assert total == 5
        assert len(items) == 2

    def test_scoped_to_run(self, db):
        user = _make_user(db)
        run_a = _make_paper_run(db, user.id, run_id="run_a")
        run_b = _make_paper_run(db, user.id, run_id="run_b")
        _make_trade(
            db,
            run_a.id,
            id="tr_a",
            entry_ts=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )
        _make_trade(
            db,
            run_b.id,
            id="tr_b",
            entry_ts=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )

        items, total = paper_service.list_trades_for_run(db, run_a.id)
        assert total == 1
        assert items[0].id == "tr_a"


# ─────────────────────────────────────────────────────────────────────
# list_decisions_for_run
# ─────────────────────────────────────────────────────────────────────


class TestListDecisionsForRun:
    def test_filters_by_paper_run_id_and_allowlist(self, db):
        user = _make_user(db)
        base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

        # A matching row + a wrong-paper_run_id row + a non-paper action.
        _make_decision(
            db,
            id="d_ok",
            action="USER_ORDER_PLACED",
            paper_run_id="run_test",
            ts=base,
        )
        _make_decision(
            db,
            id="d_wrong_run",
            action="USER_ORDER_PLACED",
            paper_run_id="run_other",
            ts=base + timedelta(seconds=1),
        )
        _make_decision(
            db,
            id="d_non_paper_action",
            action="validate",  # evolution runner action, not paper
            paper_run_id="run_test",
            ts=base + timedelta(seconds=2),
        )

        items, total = paper_service.list_decisions_for_run(db, "run_test")

        assert total == 1
        assert items[0].id == "d_ok"

    def test_newest_first(self, db):
        user = _make_user(db)
        base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        for i in range(3):
            _make_decision(
                db,
                id=f"d_{i}",
                action="USER_ORDER_PLACED",
                paper_run_id="run_test",
                ts=base + timedelta(seconds=i),
            )

        items, _total = paper_service.list_decisions_for_run(db, "run_test")

        assert [d.id for d in items] == ["d_2", "d_1", "d_0"]

    def test_since_filter(self, db):
        user = _make_user(db)
        base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        _make_decision(
            db,
            id="d_before",
            action="USER_ORDER_PLACED",
            paper_run_id="run_test",
            ts=base,
        )
        _make_decision(
            db,
            id="d_after",
            action="USER_ORDER_PLACED",
            paper_run_id="run_test",
            ts=base + timedelta(seconds=30),
        )

        items, total = paper_service.list_decisions_for_run(
            db, "run_test", since=base + timedelta(seconds=15)
        )

        assert total == 1
        assert items[0].id == "d_after"


# ─────────────────────────────────────────────────────────────────────
# list_equity_for_run
# ─────────────────────────────────────────────────────────────────────


class TestListEquityForRun:
    def test_chronological_order(self, db):
        user = _make_user(db)
        run = _make_paper_run(db, user.id)
        base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        for i in [2, 0, 1]:  # Insert out of order to check the ORDER BY.
            db.add(
                models.PaperEquitySnapshot(
                    id=f"snap_{i}",
                    run_id=run.id,
                    timestamp=base + timedelta(minutes=i),
                    balance=10_000.0 + i,
                    unrealized_pnl=0.0,
                )
            )
        db.commit()

        items = paper_service.list_equity_for_run(db, run.id)

        assert [s.id for s in items] == ["snap_0", "snap_1", "snap_2"]

    def test_from_to_window(self, db):
        user = _make_user(db)
        run = _make_paper_run(db, user.id)
        base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        for i in range(5):
            db.add(
                models.PaperEquitySnapshot(
                    id=f"snap_{i}",
                    run_id=run.id,
                    timestamp=base + timedelta(minutes=i),
                    balance=10_000.0,
                    unrealized_pnl=0.0,
                )
            )
        db.commit()

        items = paper_service.list_equity_for_run(
            db,
            run.id,
            from_ts=base + timedelta(minutes=1),
            to_ts=base + timedelta(minutes=3),
        )

        assert [s.id for s in items] == ["snap_1", "snap_2", "snap_3"]


# ─────────────────────────────────────────────────────────────────────
# get_backtest_overlay
# ─────────────────────────────────────────────────────────────────────


class TestGetBacktestOverlay:
    def test_no_genome_returns_no_associated(self, db):
        user = _make_user(db)
        run = _make_paper_run(db, user.id)
        # `strategy_genome_id` is None → no evolution link.
        result = paper_service.get_backtest_overlay(db, run)
        assert result["source"] == "unavailable"
        assert result["reason"] == "no_associated_evolution_run"
        assert result["items"] == []

    def test_no_oos_folds_returns_no_persisted(self, db):
        user = _make_user(db)
        # Build the full chain but with no ValidationFold rows.
        evo = models.EvolutionRun(
            id="evorun_x",
            status=models.EvolutionRunStatus.COMPLETED,
            instrument="EUR_USD",
            granularity="M1",
            config_json={},
            stop_conditions_json={},
            current_generation=1,
            llm_cost_usd=0,
            created_by=user.id,
        )
        gen = models.Generation(id="gen_x_1", run_id="evorun_x", index=1)
        genome = models.StrategyGenome(
            id="genome_x_1_0",
            generation_id="gen_x_1",
            dsl_json={"stub": True},
            parent_ids=[],
            origin=models.GenomeOrigin.SEED,
            validator_passed=False,
            validation_failures=[],
            fitness=0.5,
        )
        db.add_all([evo, gen, genome])
        db.commit()

        run = _make_paper_run(db, user.id, run_id="run_no_oos")
        run.strategy_genome_id = genome.id
        db.commit()

        result = paper_service.get_backtest_overlay(db, run)

        assert result["source"] == "unavailable"
        assert result["reason"] == "no_oos_equity_persisted"

    def test_oos_equity_series_not_persisted_reason(self, db):
        """OOS folds exist but neither `oos_equity_series` nor
        `equity_series` attribute is populated on any fold. The
        overlay endpoint returns `oos_equity_series_not_persisted`
        as the honest reason label (v0.6.1 UAT fix — the previous
        `genome_predates_v0_4_0` label was misleading because the
        column was planned for v0.4.0 but never actually shipped)."""
        user = _make_user(db)
        evo = models.EvolutionRun(
            id="evorun_y",
            status=models.EvolutionRunStatus.COMPLETED,
            instrument="EUR_USD",
            granularity="M1",
            config_json={},
            stop_conditions_json={},
            current_generation=1,
            llm_cost_usd=0,
            created_by=user.id,
        )
        gen = models.Generation(id="gen_y_1", run_id="evorun_y", index=1)
        genome = models.StrategyGenome(
            id="genome_y_1_0",
            generation_id="gen_y_1",
            dsl_json={"stub": True},
            parent_ids=[],
            origin=models.GenomeOrigin.SEED,
            validator_passed=False,
            validation_failures=[],
            fitness=0.5,
        )
        db.add_all([evo, gen, genome])
        # Add one OOS fold but no equity series attribute.
        fold = models.ValidationFold(
            id="fold_y_1_0",
            genome_id=genome.id,
            fold_index=0,
            fold_start=datetime(2026, 1, 1, tzinfo=UTC),
            fold_end=datetime(2026, 2, 1, tzinfo=UTC),
            is_oos=True,
            passed_gate_b=False,
        )
        db.add(fold)
        db.commit()

        run = _make_paper_run(db, user.id, run_id="run_legacy")
        run.strategy_genome_id = genome.id
        db.commit()

        result = paper_service.get_backtest_overlay(db, run)

        assert result["source"] == "unavailable"
        assert result["reason"] == "oos_equity_series_not_persisted"
