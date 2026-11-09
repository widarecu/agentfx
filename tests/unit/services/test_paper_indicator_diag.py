"""AFX-48: per-bar live-vs-backtest entry-condition diagnostic.

genome_gen_evorun_20261027_dcef6ac7_027_012
(`stoch(16,3) <= 20.3` long / `stoch(14,3) > 80` short) produced zero
live trades on AUD_USD M30 while its OOS backtest fold logged 189. The
runner had no per-bar visibility into what it computed, so there was no
way to tell a candle-feed problem from an indicator-arg problem from a
threshold-comparison problem. These tests lock the diagnostic that
`_signal_tick` now emits for every walked bar.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from agentfx.core.dsl import StrategySpec, compile_spec
from agentfx.core.dsl.indicators import evaluate_indicator
from agentfx.services import paper_indicator_diag, paper_runner
from agentfx.services.paper_indicator_diag import (
    DIVERGENCE_ACTION,
    diagnose_entry_bars,
)
from agentfx.services.paper_runner import _signal_tick

pytestmark = pytest.mark.unit

M30 = 1800


def _genome_dsl(long_args=(16, 3), short_args=(14, 3)) -> dict:
    return {
        "entry": {
            "long": {
                "lhs": {"ind": "stoch", "args": list(long_args)},
                "cmp": "<=",
                "rhs": 20.3,
            },
            "short": {
                "lhs": {"ind": "stoch", "args": list(short_args)},
                "cmp": ">",
                "rhs": 80,
            },
        },
        "exit": {
            "stop_pips": 20,
            "target_pips": 40,
            "max_hold_bars": 48,
            "min_hold_bars": 0,
            "opposite_signal": True,
        },
        "size": {"risk_pct": 0.005},
    }


def _candles(bars: int, latest_ts: datetime, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 0.65 + np.cumsum(rng.normal(0, 0.0008, bars))
    high = close + np.abs(rng.normal(0, 0.0004, bars))
    low = close - np.abs(rng.normal(0, 0.0004, bars))
    index = pd.DatetimeIndex(
        [latest_ts - timedelta(seconds=M30 * (bars - 1 - i)) for i in range(bars)],
        tz="UTC",
    )
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 100.0},
        index=index,
    )


def _run(dsl: dict, approved_at: datetime, bookmark: datetime | None):
    from agentfx.api.models import RunStatus

    params = {
        "dsl_json": dsl,
        "fold_length_days": 22,
        "current_fold_index": 0,
    }
    if bookmark is not None:
        params["last_evaluated_bar_ts"] = bookmark.isoformat()
    return SimpleNamespace(
        id="run_1793096698848_paper_AUD_USD_M30_82abbd95ab",
        pair="AUD_USD",
        user_id=1,
        status=RunStatus.RUNNING,
        approved_at=approved_at,
        snapshot_balance=10_000.0,
        current_balance=10_000.0,
        pnl=0.0,
        parameters=params,
        risk_config={"max_position_size_units": 10_000},
    )


def _db() -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    return db


def _client() -> MagicMock:
    client = MagicMock()
    client.get_position.return_value = {"long_units": 0, "short_units": 0}
    client.place_market_order.return_value = {
        "trade_id": "t1",
        "fill_price": 0.65,
        "units": 1000,
    }
    client.get_account_balance.return_value = {"balance": 10_000.0, "currency": "USD"}
    return client


def _fetch(live_df: pd.DataFrame, reference_df: pd.DataFrame):
    calls: list[dict] = []

    def fetch(instrument, granularity, bars=None, *, from_time=None, to_time=None):
        calls.append({"bars": bars, "from_time": from_time})
        return live_df if from_time is not None else reference_df

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _divergence_logs(db: MagicMock) -> list:
    return [
        c.args[0]
        for c in db.add.call_args_list
        if getattr(c.args[0], "action", None) == DIVERGENCE_ACTION
    ]


class TestSignalTickEmitsPerBarDiagnostic:
    def test_logs_live_stoch_value_threshold_and_check_each_walked_bar(
        self, caplog, monkeypatch
    ):
        monkeypatch.setattr(paper_runner, "_execute_walk_transitions", MagicMock())
        latest = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
        reference_df = _candles(300, latest)
        live_df = reference_df.iloc[-120:]
        dsl = _genome_dsl()
        strategy = compile_spec(StrategySpec.model_validate(dsl), pip_size=0.0001)
        bookmark = live_df.index[-4].to_pydatetime()
        run = _run(dsl, approved_at=live_df.index[0].to_pydatetime(), bookmark=bookmark)

        with caplog.at_level(
            logging.INFO, logger="agentfx.services.paper_indicator_diag"
        ):
            _signal_tick(
                db=_db(),
                run=run,
                client=_client(),
                strategy=strategy,
                candle_fetch_fn=_fetch(live_df, reference_df),
                instrument="AUD_USD",
                granularity_seconds=M30,
                now_utc=latest + timedelta(seconds=M30 + 10),
            )

        records = [
            r.diag for r in caplog.records if getattr(r, "diag", None) is not None
        ]
        # 3 bars past the bookmark × 2 comparisons (long + short).
        assert len(records) == 6
        expected_k16 = evaluate_indicator(live_df, "stoch", [16, 3])
        long_records = [r for r in records if r["path"] == "long"]
        assert [r["bar_ts"] for r in long_records] == [
            ts.isoformat() for ts in live_df.index[-3:]
        ]
        for rec in long_records:
            ts = pd.Timestamp(rec["bar_ts"])
            assert rec["lhs"]["label"] == "stoch[16,3]"
            assert rec["lhs"]["live"] == pytest.approx(float(expected_k16.loc[ts]))
            # Warm window → backtest-path reference agrees with live.
            assert rec["lhs"]["reference"] == pytest.approx(rec["lhs"]["live"])
            assert rec["cmp"] == "<="
            assert rec["rhs"]["live"] == pytest.approx(20.3)
            assert rec["live_pass"] is (float(expected_k16.loc[ts]) <= 20.3)
            assert rec["manual_pass"] is rec["live_pass"]
            assert rec["issues"] == []
        short = [r for r in records if r["path"] == "short"][0]
        assert short["lhs"]["label"] == "stoch[14,3]"
        assert short["cmp"] == ">"

    def test_fold_anchor_warmup_starvation_flagged_once(self):
        """Fold-anchored live window shorter than stoch's lookback →
        live NaN while the warm backtest-path reference is finite."""
        latest = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
        reference_df = _candles(300, latest)
        live_df = reference_df.iloc[-10:]
        dsl = _genome_dsl()
        strategy = compile_spec(StrategySpec.model_validate(dsl), pip_size=0.0001)
        run = _run(
            dsl,
            approved_at=live_df.index[0].to_pydatetime(),
            bookmark=live_df.index[-2].to_pydatetime(),
        )
        db = _db()
        fetch = _fetch(live_df, reference_df)
        now = latest + timedelta(seconds=M30 + 10)

        _signal_tick(
            db=db,
            run=run,
            client=_client(),
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="AUD_USD",
            granularity_seconds=M30,
            now_utc=now,
        )

        logs = _divergence_logs(db)
        assert len(logs) == 1
        issues = logs[0].outputs_json["issues"]
        assert {"path": "long", "issue": "live_nan_warmup"} in issues
        assert {"path": "short", "issue": "live_nan_warmup"} in issues

        # Same divergence on the next bar → no second decision-log row.
        run.parameters["last_evaluated_bar_ts"] = live_df.index[-2].isoformat()
        _signal_tick(
            db=db,
            run=run,
            client=_client(),
            strategy=strategy,
            candle_fetch_fn=fetch,
            instrument="AUD_USD",
            granularity_seconds=M30,
            now_utc=now,
        )
        assert len(_divergence_logs(db)) == 1

    def test_diagnostic_failure_never_breaks_the_tick(self, monkeypatch):
        latest = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
        df = _candles(120, latest)
        dsl = _genome_dsl()
        strategy = compile_spec(StrategySpec.model_validate(dsl), pip_size=0.0001)
        run = _run(dsl, approved_at=df.index[0].to_pydatetime(), bookmark=None)

        def boom(**_kwargs):
            raise RuntimeError("diag exploded")

        monkeypatch.setattr(paper_indicator_diag, "diagnose_entry_bars", boom)
        monkeypatch.setattr(paper_runner, "_execute_walk_transitions", MagicMock())
        _signal_tick(
            db=_db(),
            run=run,
            client=_client(),
            strategy=strategy,
            candle_fetch_fn=_fetch(df, df),
            instrument="AUD_USD",
            granularity_seconds=M30,
            now_utc=latest + timedelta(seconds=M30 + 10),
        )
        assert run.parameters["last_evaluated_bar_ts"] == latest.isoformat()


class TestDiagnoseEntryBars:
    def _setup(self, dsl_strategy=None, dsl_stored=None):
        latest = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
        df = _candles(200, latest)
        strategy = compile_spec(
            StrategySpec.model_validate(dsl_strategy or _genome_dsl()), pip_size=0.0001
        )
        return df, strategy, dsl_stored or _genome_dsl()

    def test_arg_parse_mismatch_between_running_spec_and_stored_dsl(self):
        df, strategy, dsl = self._setup(dsl_strategy=_genome_dsl(long_args=(14, 3)))
        records = diagnose_entry_bars(
            strategy=strategy,
            live_df=df,
            reference_df=df,
            raw_entries=strategy.raw_entry_signals(df),
            bar_index=df.index[-1:],
            dsl_json=dsl,
        )
        long = [r for r in records if r["path"] == "long"][0]
        assert "arg_parse" in long["issues"]
        assert long["lhs"]["label"] == "stoch[14,3]"
        assert long["lhs"]["reference_label"] == "stoch[16,3]"

    def test_candle_mismatch_when_live_close_differs_from_reference(self):
        df, strategy, dsl = self._setup()
        reference = df.copy()
        reference.iloc[-1, reference.columns.get_loc("close")] += 0.01
        records = diagnose_entry_bars(
            strategy=strategy,
            live_df=df,
            reference_df=reference,
            raw_entries=strategy.raw_entry_signals(df),
            bar_index=df.index[-1:],
            dsl_json=dsl,
        )
        assert all("candle_mismatch" in r["issues"] for r in records)

    def test_missing_reference_bar_flags_candle_mismatch(self):
        df, strategy, dsl = self._setup()
        records = diagnose_entry_bars(
            strategy=strategy,
            live_df=df,
            reference_df=df.iloc[:-1],
            raw_entries=strategy.raw_entry_signals(df),
            bar_index=df.index[-1:],
            dsl_json=dsl,
        )
        assert all("candle_mismatch" in r["issues"] for r in records)

    def test_threshold_compare_mismatch_detected(self, monkeypatch):
        df, strategy, dsl = self._setup()
        real = paper_indicator_diag._eval_comparison

        def inverted(cmp, frame, cache):
            return ~real(cmp, frame, cache).fillna(False).astype(bool)

        monkeypatch.setattr(paper_indicator_diag, "_eval_comparison", inverted)
        records = diagnose_entry_bars(
            strategy=strategy,
            live_df=df,
            reference_df=df,
            raw_entries=strategy.raw_entry_signals(df),
            bar_index=df.index[-1:],
            dsl_json=dsl,
        )
        assert all("threshold_compare" in r["issues"] for r in records)

    def test_crosses_comparator_manual_check_uses_previous_bar(self):
        dsl = _genome_dsl()
        dsl["entry"]["long"] = {
            "lhs": {"ind": "stoch", "args": [16, 3]},
            "cmp": "crosses_above",
            "rhs": 20.3,
        }
        df, strategy, _ = self._setup(dsl_strategy=dsl)
        cond = evaluate_indicator(df, "stoch", [16, 3])
        crossed = (cond.shift(1) <= 20.3) & (cond > 20.3)
        records = diagnose_entry_bars(
            strategy=strategy,
            live_df=df,
            reference_df=df,
            raw_entries=strategy.raw_entry_signals(df),
            bar_index=df.index[20:],
            dsl_json=dsl,
        )
        long = [r for r in records if r["path"] == "long"]
        assert [r["live_pass"] for r in long] == [bool(x) for x in crossed.iloc[20:]]
        assert all(r["manual_pass"] is r["live_pass"] for r in long)
        assert all(r["issues"] == [] for r in long)

    def test_compound_conditions_get_one_record_per_comparison(self):
        dsl = _genome_dsl()
        dsl["entry"]["long"] = {
            "op": "AND",
            "args": [
                {"lhs": {"ind": "stoch", "args": [16, 3]}, "cmp": "<=", "rhs": 20.3},
                {"lhs": {"ind": "rsi", "args": [14]}, "cmp": "<", "rhs": 40},
            ],
        }
        df, strategy, _ = self._setup(dsl_strategy=dsl)
        records = diagnose_entry_bars(
            strategy=strategy,
            live_df=df,
            reference_df=df,
            raw_entries=strategy.raw_entry_signals(df),
            bar_index=df.index[-1:],
            dsl_json=dsl,
        )
        assert sorted(r["path"] for r in records) == [
            "long.AND[0]",
            "long.AND[1]",
            "short",
        ]

    def test_duck_typed_strategy_returns_no_records(self):
        df = _candles(20, datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
        strategy = SimpleNamespace(spec=SimpleNamespace(entry=SimpleNamespace()))
        assert (
            diagnose_entry_bars(
                strategy=strategy,
                live_df=df,
                reference_df=df,
                raw_entries=pd.Series(0, index=df.index),
                bar_index=df.index[-1:],
                dsl_json=None,
            )
            == []
        )
