"""Unit tests for paper-service forward-test locking, evaluation, and router endpoints (AFX-154)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from agentfx.api import models
from agentfx.api.routers.paper import (
    _to_public,
    check_paper_run_live_eligibility,
    get_paper_run_forward_test,
)
from agentfx.api.schemas.paper import DeployPaperRequest
from agentfx.api.services import paper as paper_service
from agentfx.core.forward_test import ForwardTestContract


def _make_closed_trades(
    count: int,
    *,
    start: datetime,
    pnl_pattern: list[float],
    financing: float = 0.0,
    commission: float = 0.0,
) -> list[SimpleNamespace]:
    trades = []
    for i in range(count):
        entry = start + timedelta(hours=6 * i)
        exit_ts = entry + timedelta(hours=4)
        trades.append(
            SimpleNamespace(
                id=f"tr_{i}",
                run_id="run_ft_test",
                entry_timestamp=entry,
                exit_timestamp=exit_ts,
                pnl=pnl_pattern[i % len(pnl_pattern)],
                financing=financing,
                commission=commission,
            )
        )
    return trades


def _mock_db_for_eval(
    closed_trades: list[SimpleNamespace],
    snapshots: list[SimpleNamespace] | None = None,
) -> MagicMock:
    db = MagicMock()
    trade_q = MagicMock()
    trade_q.filter.return_value = trade_q
    trade_q.order_by.return_value = trade_q
    trade_q.all.return_value = closed_trades

    snap_q = MagicMock()
    snap_q.filter.return_value = snap_q
    snap_q.order_by.return_value = snap_q
    snap_q.limit.return_value = snap_q
    snap_q.all.return_value = list(reversed(snapshots or []))

    def _query(model):
        if model is models.Trade:
            return trade_q
        if model is models.PaperEquitySnapshot:
            return snap_q
        return MagicMock()

    db.query.side_effect = _query
    return db


class TestApproveLocksForwardTestContract:
    def test_approve_and_deploy_persists_locked_contract_in_run_and_decision_log(
        self,
    ) -> None:
        now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
        oos_folds = [
            SimpleNamespace(
                is_oos=True,
                fold_start=now - timedelta(days=90),
                fold_end=now - timedelta(days=60),
                sharpe=1.4,
                hit_rate=0.52,
                max_drawdown=-0.04,
            ),
            SimpleNamespace(
                is_oos=True,
                fold_start=now - timedelta(days=60),
                fold_end=now - timedelta(days=30),
                sharpe=1.8,
                hit_rate=0.56,
                max_drawdown=-0.07,
            ),
        ]
        genome = SimpleNamespace(
            id="genome_1",
            dsl_json={"entry": {}, "exit": {}},
            validation_folds=oos_folds,
            generation=SimpleNamespace(run_id="evo_1"),
        )
        creds = SimpleNamespace(oanda_environment="practice")
        user = SimpleNamespace(
            id=7, email="wilson@example.com", role=models.UserRole.TRADER
        )
        body = DeployPaperRequest(
            strategy_genome_id="genome_1",
            instrument="EUR_USD",
            granularity="H1",
            oanda_account_id="101-004-00000-001",
        )
        db = MagicMock()
        added: list[object] = []
        db.add.side_effect = added.append

        with (
            patch.object(
                paper_service, "_load_owned_validated_genome", return_value=genome
            ),
            patch.object(
                paper_service, "_load_verified_credentials", return_value=creds
            ),
            patch.object(paper_service, "_validate_account_choice"),
            patch.object(paper_service, "_reject_if_account_has_active_run"),
            patch.object(paper_service, "_bind_pool_account_to_run"),
        ):
            run = paper_service.approve_and_deploy(db, body, current_user=user)

        contract = run.parameters["forward_test_contract"]
        assert contract["min_closed_trades"] == 100
        assert contract["min_net_pnl"] == 0.0
        assert contract["min_sharpe"] == 1.0
        assert contract["max_drawdown"] == 0.10
        assert contract["max_divergence"] == 0.30
        assert contract["backtest_oos_sharpe"] == pytest.approx(1.6)
        assert contract["backtest_oos_hit_rate"] == pytest.approx(0.54)
        assert contract["backtest_oos_max_drawdown"] == pytest.approx(-0.07)
        assert contract["locked_at"]

        # Same locked contract is audited on the USER_APPROVE_GENOME DecisionLog row.
        decision_logs = [obj for obj in added if isinstance(obj, models.DecisionLog)]
        assert len(decision_logs) == 1
        assert decision_logs[0].outputs_json["forward_test_contract"] == contract

        # Exposed on PaperRunPublic without extra DB roundtrips.
        pub = _to_public(run)
        assert pub.forward_test_contract == contract


class TestServiceEvaluationAndLiveEligibility:
    def _make_run(
        self, contract: dict | None, *, started_at: datetime
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id="run_ft_test",
            user_id=7,
            status=models.RunStatus.RUNNING,
            pair="EUR_USD",
            parameters={"granularity": "H1", "forward_test_contract": contract}
            if contract is not None
            else {"granularity": "H1"},
            snapshot_balance=10_000.0,
            initial_balance=10_000.0,
            max_drawdown=None,
            started_at=started_at,
            created_at=started_at,
            completed_at=None,
        )

    def test_insufficient_trades_refuses_live_eligibility(self) -> None:
        start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
        contract = ForwardTestContract.create(
            min_closed_trades=100, backtest_oos_sharpe=1.5, locked_at=start
        ).to_dict()
        run = self._make_run(contract, started_at=start)
        trades = _make_closed_trades(11, start=start, pnl_pattern=[25.0, -5.0, 20.0])
        db = _mock_db_for_eval(trades)

        ev = paper_service.evaluate_paper_run_forward_test(db, run)
        assert ev.status == "in_progress"
        assert ev.eligible_for_live is False
        assert ev.progress_label == "11 / 100 trades"

        with pytest.raises(paper_service.LiveEligibilityError) as exc:
            paper_service.assert_live_eligible(db, run)
        assert exc.value.evaluation.status == "in_progress"

    def test_passing_run_clears_live_eligibility(self) -> None:
        start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        contract = ForwardTestContract.create(
            min_closed_trades=100,
            min_sharpe=1.0,
            max_drawdown=0.10,
            max_divergence=0.30,
            backtest_oos_sharpe=1.5,
            locked_at=start,
        ).to_dict()
        run = self._make_run(contract, started_at=start)
        # Steady positive daily returns -> high Sharpe, tiny drawdown, net PnL > 0.
        trades = _make_closed_trades(
            100, start=start, pnl_pattern=[40.0, 35.0, -10.0, 30.0]
        )
        db = _mock_db_for_eval(trades)

        ev = paper_service.assert_live_eligible(db, run)
        assert ev.status == "passed"
        assert ev.eligible_for_live is True
        assert ev.closed_trades == 100
        assert ev.failures == []

    def test_failing_run_rejected_when_net_pnl_negative_after_costs(self) -> None:
        start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        contract = ForwardTestContract.create(
            min_closed_trades=100, locked_at=start
        ).to_dict()
        run = self._make_run(contract, started_at=start)
        # Gross +5 per trade, but -8 commission => net -3 per trade.
        trades = _make_closed_trades(
            100, start=start, pnl_pattern=[5.0], commission=8.0
        )
        db = _mock_db_for_eval(trades)

        with pytest.raises(paper_service.LiveEligibilityError) as exc:
            paper_service.assert_live_eligible(db, run)
        assert exc.value.evaluation.status == "failed"
        assert exc.value.evaluation.metrics["net_pnl"] == pytest.approx(-300.0)

    def test_equity_snapshot_drawdown_triggers_early_failure(self) -> None:
        start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
        contract = ForwardTestContract.create(
            min_closed_trades=100, max_drawdown=0.10, locked_at=start
        ).to_dict()
        run = self._make_run(contract, started_at=start)
        trades = _make_closed_trades(10, start=start, pnl_pattern=[10.0])
        snapshots = [
            SimpleNamespace(timestamp=start, balance=10_000.0, unrealized_pnl=0.0),
            SimpleNamespace(
                timestamp=start + timedelta(hours=1),
                balance=10_000.0,
                unrealized_pnl=-1_200.0,  # 12% peak-to-trough drawdown
            ),
        ]
        db = _mock_db_for_eval(trades, snapshots=snapshots)

        ev = paper_service.evaluate_paper_run_forward_test(db, run)
        assert ev.status == "failed"
        assert ev.early_failure is True
        assert ev.eligible_for_live is False


class TestRouterForwardTestEndpoints:
    def test_get_forward_test_returns_progress(self) -> None:
        start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
        contract = ForwardTestContract.create(
            min_closed_trades=100, locked_at=start
        ).to_dict()
        run = SimpleNamespace(
            id="run_ft_1",
            user_id=7,
            status=models.RunStatus.RUNNING,
            pair="EUR_USD",
            parameters={"granularity": "H1", "forward_test_contract": contract},
            snapshot_balance=10_000.0,
            initial_balance=10_000.0,
            max_drawdown=None,
            started_at=start,
            created_at=start,
            completed_at=None,
        )
        user = SimpleNamespace(id=7, role=models.UserRole.TRADER)
        db = _mock_db_for_eval(_make_closed_trades(11, start=start, pnl_pattern=[20.0]))

        with patch.object(paper_service, "get_paper_run", return_value=run):
            resp = get_paper_run_forward_test("run_ft_1", db=db, current_user=user)

        assert resp.run_id == "run_ft_1"
        assert resp.status == "in_progress"
        assert resp.eligible_for_live is False
        assert resp.progress_label == "11 / 100 trades"
        assert resp.contract is not None
        assert resp.contract.min_closed_trades == 100

    def test_post_live_eligibility_returns_409_when_not_eligible(self) -> None:
        start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
        contract = ForwardTestContract.create(
            min_closed_trades=100, locked_at=start
        ).to_dict()
        run = SimpleNamespace(
            id="run_ft_1",
            user_id=7,
            status=models.RunStatus.RUNNING,
            pair="EUR_USD",
            parameters={"granularity": "H1", "forward_test_contract": contract},
            snapshot_balance=10_000.0,
            initial_balance=10_000.0,
            max_drawdown=None,
            started_at=start,
            created_at=start,
            completed_at=None,
        )
        user = SimpleNamespace(id=7, role=models.UserRole.TRADER)
        db = _mock_db_for_eval(_make_closed_trades(11, start=start, pnl_pattern=[20.0]))

        with (
            patch.object(paper_service, "get_paper_run", return_value=run),
            pytest.raises(HTTPException) as exc,
        ):
            check_paper_run_live_eligibility("run_ft_1", db=db, current_user=user)

        assert exc.value.status_code == 409
        assert exc.value.detail["status"] == "in_progress"
        assert exc.value.detail["progress_label"] == "11 / 100 trades"

    def test_post_live_eligibility_returns_200_when_passing(self) -> None:
        start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        contract = ForwardTestContract.create(
            min_closed_trades=100, backtest_oos_sharpe=1.5, locked_at=start
        ).to_dict()
        run = SimpleNamespace(
            id="run_ft_1",
            user_id=7,
            status=models.RunStatus.RUNNING,
            pair="EUR_USD",
            parameters={"granularity": "H1", "forward_test_contract": contract},
            snapshot_balance=10_000.0,
            initial_balance=10_000.0,
            max_drawdown=None,
            started_at=start,
            created_at=start,
            completed_at=None,
        )
        user = SimpleNamespace(id=7, role=models.UserRole.TRADER)
        db = _mock_db_for_eval(
            _make_closed_trades(100, start=start, pnl_pattern=[40.0, 35.0, -10.0, 30.0])
        )

        with patch.object(paper_service, "get_paper_run", return_value=run):
            resp = check_paper_run_live_eligibility(
                "run_ft_1", db=db, current_user=user
            )

        assert resp.status == "passed"
        assert resp.eligible_for_live is True
        assert resp.failures == []


class TestContractImmutability:
    def test_update_run_preserves_locked_contract_and_blocks_forgery(self):
        from agentfx.api import schemas
        from agentfx.api.services import run as run_service

        db = MagicMock()
        locked = ForwardTestContract.create(
            min_closed_trades=100,
            min_sharpe=1.0,
            max_drawdown=0.10,
            max_divergence=0.30,
        ).to_dict()
        run = SimpleNamespace(
            id="run_locked",
            user_id="u_owner",
            parameters={"name": "orig", "forward_test_contract": locked},
            started_at=None,
            completed_at=None,
            updated_at=None,
        )

        updated = run_service.update_run(
            db,
            db_run=run,
            run_in=schemas.RunUpdate(
                parameters={
                    "name": "renamed",
                    "forward_test_contract": {
                        "min_closed_trades": 1,
                        "min_sharpe": -10.0,
                        "max_drawdown": 1.0,
                    },
                }
            ),
        )
        assert updated.parameters["name"] == "renamed"
        assert updated.parameters["forward_test_contract"] == locked

        # Clearing parameters must still preserve the locked contract.
        cleared = run_service.update_run(
            db,
            db_run=run,
            run_in=schemas.RunUpdate(parameters={}),
        )
        assert cleared.parameters["forward_test_contract"] == locked

        # A legacy run without a contract cannot have one forged via update_run.
        legacy = SimpleNamespace(
            id="run_legacy",
            user_id="u_owner",
            parameters={"name": "legacy"},
            started_at=None,
            completed_at=None,
            updated_at=None,
        )
        forged = run_service.update_run(
            db,
            db_run=legacy,
            run_in=schemas.RunUpdate(
                parameters={"forward_test_contract": {"min_closed_trades": 1}}
            ),
        )
        assert "forward_test_contract" not in forged.parameters

    def test_put_run_rejects_non_owner(self):
        from agentfx.api import schemas
        from agentfx.api.routers.runs import update_run as router_update_run
        from agentfx.api.services import run as run_service

        db = MagicMock()
        other = SimpleNamespace(id="u_other", role=models.UserRole.TRADER)
        run = SimpleNamespace(
            id="run_owned",
            user_id="u_owner",
            parameters={
                "forward_test_contract": ForwardTestContract.create(
                    min_closed_trades=100
                ).to_dict()
            },
        )

        with (
            patch.object(run_service, "get_run", return_value=run),
            pytest.raises(HTTPException) as exc_info,
        ):
            router_update_run(
                run_id=run.id,
                run_in=schemas.RunUpdate(parameters={"forward_test_contract": {}}),
                db=db,
                current_user=other,
            )
        assert exc_info.value.status_code == 404
