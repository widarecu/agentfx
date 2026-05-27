"""DSL dispatch through `services.backtest.make_strategy()`.

The orchestrator (US-003) will construct StrategySpec genomes and feed them
into the existing backtest engine via the v0.2.0 `make_strategy()` entry
point. These tests confirm both the StrategySpec-instance and dict-form
paths work, and that the resulting Strategy produces the same signals as
direct compilation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agentfx.core.dsl import StrategySpec, compile_spec
from agentfx.core.dsl.seeds import MA_CROSSOVER_SEED, ma_crossover_seed
from agentfx.services.backtest import make_strategy


def _make_synthetic_df(n: int = 200, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    returns = rng.normal(0, 0.0005, n)
    close = 1.10 * np.exp(np.cumsum(returns))
    spread = np.abs(rng.normal(0, 0.0003, n))
    return pd.DataFrame(
        {
            "open": close,
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )


class TestDslDispatch:
    def test_accepts_strategy_spec_instance(self) -> None:
        spec = ma_crossover_seed(fast=5, slow=20)
        strat = make_strategy("dsl", spec=spec)
        assert isinstance(strat, type(compile_spec(spec)))

    def test_accepts_dict_form(self) -> None:
        spec = ma_crossover_seed(fast=5, slow=20)
        strat = make_strategy("dsl", spec=spec.model_dump(mode="json"))
        # Same compiled type as the instance path
        df = _make_synthetic_df()
        out_dict = strat.generate_signals(df)
        out_inst = make_strategy("dsl", spec=spec).generate_signals(df)
        pd.testing.assert_series_equal(out_dict, out_inst, check_names=False)

    def test_dispatch_signals_equal_direct_compile(self) -> None:
        df = _make_synthetic_df(500, seed=11)
        spec = MA_CROSSOVER_SEED
        via_dispatch = make_strategy("dsl", spec=spec).generate_signals(df)
        via_compile = compile_spec(spec).generate_signals(df)
        pd.testing.assert_series_equal(via_dispatch, via_compile, check_names=False)

    def test_dispatch_round_trip_via_json(self) -> None:
        # Mirrors how the orchestrator will actually serialise specs:
        # store as JSON in strategy_genome.dsl_json, reconstruct here.
        import json

        spec = ma_crossover_seed(fast=10, slow=30)
        as_json = json.dumps(spec.model_dump(mode="json"))
        round_tripped = json.loads(as_json)
        strat = make_strategy("dsl", spec=round_tripped)
        df = _make_synthetic_df()
        out = strat.generate_signals(df)
        assert set(out.unique()).issubset({-1, 0, 1})

    def test_missing_spec_kwarg_rejected(self) -> None:
        with pytest.raises(ValueError, match="requires a 'spec' kwarg"):
            make_strategy("dsl")

    def test_invalid_spec_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="'spec' must be"):
            make_strategy("dsl", spec="not a spec")

    def test_invalid_dict_payload_rejected(self) -> None:
        # Dict that doesn't conform to StrategySpec schema → ValidationError
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            make_strategy("dsl", spec={"version": 1, "entry": "garbage"})

    def test_named_strategies_still_dispatch(self) -> None:
        # Backward compat: existing callers using "ma", "rsi", "donchian"
        # must still work unchanged.
        from agentfx.core.strategy import (
            DonchianBreakout,
            MACrossover,
            RSIMeanReversion,
        )

        assert isinstance(make_strategy("ma", fast=10, slow=30), MACrossover)
        assert isinstance(make_strategy("rsi", window=14), RSIMeanReversion)
        assert isinstance(make_strategy("donchian", window=20), DonchianBreakout)


class TestDslDispatchSignalEquivalence:
    """The DSL dispatch path must match the named-strategy dispatch when the
    DSL spec encodes the same idea — confirming the make_strategy-level
    contract is consistent across both representations."""

    def test_ma_seed_via_dispatch_matches_named_dispatch(self) -> None:
        df = _make_synthetic_df(500, seed=23)
        # Via DSL dispatch
        dsl_strat = make_strategy("dsl", spec=ma_crossover_seed(fast=20, slow=50))
        # Via named dispatch
        named_strat = make_strategy("ma", fast=20, slow=50)

        dsl_sig = dsl_strat.generate_signals(df).astype(int)
        named_sig = named_strat.generate_signals(df).astype(int)

        pd.testing.assert_series_equal(dsl_sig, named_sig, check_names=False)
