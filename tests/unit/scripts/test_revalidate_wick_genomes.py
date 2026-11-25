import importlib.util
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from agentfx.agents.validator import FoldEvaluation
from agentfx.core.dsl import compiler
from agentfx.core.dsl.grammar import ExitRules
from agentfx.core.validation import WalkForwardFold

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "revalidate_wick_genomes.py"
_spec = importlib.util.spec_from_file_location("revalidate_wick_genomes", _SCRIPT)
assert _spec is not None and _spec.loader is not None
revalidate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = revalidate
_spec.loader.exec_module(revalidate)


def make_fold(index, *, is_oos=True):
    start = datetime(2022, 1, 1, tzinfo=UTC) + timedelta(days=index * 30)
    return WalkForwardFold(index, start, start + timedelta(days=30), is_oos)


def make_genome(*folds):
    return revalidate.Genome(
        "synthetic",
        True,
        {},
        "EUR_USD",
        "M30",
        {
            "effective_spread_pips": 1.0,
            "min_cagr_pct": 0.12,
            "leverage": 20,
            "account_balance": 5000,
        },
        list(folds),
        cohort=True,
        historical_paper=True,
        approved=True,
    )


@pytest.mark.parametrize(
    ("open_price", "low_price", "expected_fills"),
    [
        (1.09700, 1.09600, [1.09800, 1.09700, 1.09700]),
        (1.09804, 1.09804, [None, None, 1.09804]),
    ],
)
def test_replay_isolates_gap_fills_from_shifted_triggers(
    monkeypatch, open_price, low_price, expected_fills
):
    rules = ExitRules(
        stop_pips=20, target_pips=40, min_hold_bars=2, max_hold_bars=6, eval_mode="wick"
    )
    original = compiler._apply_exits

    def synthetic_backtest(**kwargs):
        assert kwargs["capture_signals"]
        assert kwargs["account_balance"] == 5000
        assert kwargs["leverage"] == 20
        assert kwargs["spread_pips"] == 1.0
        _, _, prices = compiler._apply_exits(
            np.array([1, 1, 1]),
            np.array([1.1, 1.099, 1.1]),
            np.array([1.1, 1.1, 1.1]),
            np.array([1.1, 1.097, low_price]),
            rules,
            0.0001,
            open_=np.array([1.1, 1.099, open_price]),
            wick_half_spread=kwargs["spread_pips"] * 0.0001 / 2,
        )
        return {"metrics": {"fill": prices.get(2)}}

    monkeypatch.setattr(revalidate.backtest, "run_backtest_from_db", synthetic_backtest)
    fold = make_fold(0)
    for (gap, shifted), expected in zip(
        ((False, False), (True, False), (True, True)), expected_fills, strict=True
    ):
        result = revalidate.replay_fold(
            make_genome(fold),
            fold,
            1.0,
            gap_aware=gap,
            shifted_trigger=shifted,
        )
        if expected is None:
            assert result["fill"] is None
        else:
            assert result["fill"] == pytest.approx(expected)
        assert compiler._apply_exits is original


def test_score_preserves_old_aggregate_and_adds_new_oos_floor():
    good = {
        "sharpe": 1.3,
        "cagr": 0.14,
        "max_drawdown": -0.06,
        "hit_rate": 0.52,
        "n_trades": 110,
    }
    bad = {**good, "cagr": -0.04, "max_drawdown": -0.11}
    folds = [
        FoldEvaluation(make_fold(0, is_oos=False), bad, False, []),
        FoldEvaluation(make_fold(1), good, False, []),
        FoldEvaluation(make_fold(2), good, False, []),
        FoldEvaluation(make_fold(3), bad, False, []),
    ]
    score = revalidate.score_folds(folds, {"min_cagr": 0.12})
    assert score.old_pass
    assert not score.new_pass
    assert score.sharpe == 1.3
    assert all(reason.startswith("OOS fold 3/3") for reason in score.fold_failures)
    assert len(score.fold_failures) == 2


def test_report_attributes_each_transition_without_counting_missing_as_failing():
    genome = make_genome(make_fold(0))
    results = [
        revalidate.Replay(
            genome,
            (
                revalidate.Score(1.2, ["Hit Rate"], []),
                revalidate.Score(1.3, [], []),
                revalidate.Score(1.4, [], ["OOS fold lost money"]),
            ),
        ),
        revalidate.replay_genome(make_genome()),
    ]
    report = revalidate.render_report(results, date(2022, 2, 1), {"unresolved"})
    assert "Stored verdict mismatches vs A: 1; missing replay inputs: 2." in report
    assert "Current paper-approved genomes failing C: 1" in report
    assert "Gap-aware fill: Fail → Pass; Fold floor: Pass → Fail" in report
    assert "No persisted out-of-sample folds" in report
    assert "Genome not found" in report
