"""Gen-0 seed strategies expressed as DSL specs.

Two groups of seeds:

1. **Classic strategy parity seeds** — re-express the original
   hand-coded strategies (MA crossover, RSI mean-reversion, Donchian
   breakout) in DSL form. The DSL-completeness gate in
   `tests/unit/dsl/test_compiler.py` asserts the compiled DSL produces
   a position series equal to the corresponding classic strategy on
   identical data, confirming the DSL is at least as expressive as the
   hand-coded strategies the platform shipped with.

2. **v0.4.0 expansion seeds** — canonical strategies for the indicator
   families added in v0.4.0 (MACD, Stochastic, Bollinger Bands,
   Ichimoku, ADX, OBV). These exist for two reasons: (a) guarantee
   every run's gen-0 population contains the new indicator families
   instead of relying on stochastic LLM proposals to surface them, and
   (b) give the GA's arity-constrained `_mut_swap_indicator` mutation a
   parent in every arg-count bucket — without a 2-arg seed (BB, MACD
   line, Stoch) and a 3-arg seed (MACD signal), those families could
   only emerge via LLM injection. The ADX and OBV seeds in particular
   exist primarily as mutation-lineage parents rather than as
   standalone strong strategies — their function alone doesn't produce
   directional signals; their value is giving the GA a starting point
   to recombine them with other indicators in later generations.

The exit-rule placeholders (large stop/target/max_hold, opposite_signal=True)
correspond to "no enforced exit beyond reversal" — matching the classic
position-driven semantics where position flips when the indicator condition
flips. In v0.4.0 when the backtest engine starts enforcing stops/targets,
seed exit rules can be tightened to realistic values.
"""

from __future__ import annotations

from agentfx.core.dsl.grammar import (
    Comparison,
    EntryConditions,
    ExitRules,
    IndicatorCall,
    SizeRules,
    StrategySpec,
)


def _ind(name: str, *args: float) -> IndicatorCall:
    return IndicatorCall(ind=name, args=list(args))  # type: ignore[arg-type]


# Permissive exit rules used by all seeds — the v1 backtest engine ignores
# these anyway; tightening to realistic values is a v0.4.0 enhancement.
_PERMISSIVE_EXIT = ExitRules(
    stop_pips=400,
    target_pips=800,
    max_hold_bars=2880,
    opposite_signal=True,
)

_DEFAULT_SIZE = SizeRules(risk_pct=0.005)


def ma_crossover_seed(fast: int = 20, slow: int = 50) -> StrategySpec:
    """MA crossover — equivalent to MACrossover(fast, slow)."""
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be < slow ({slow})")
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("sma", fast),
                cmp=">",
                rhs=_ind("sma", slow),
            ),
            short=Comparison(
                lhs=_ind("sma", fast),
                cmp="<",
                rhs=_ind("sma", slow),
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


def rsi_mean_reversion_seed(
    window: int = 14, lower: float = 30.0, upper: float = 70.0
) -> StrategySpec:
    """RSI mean-reversion — equivalent to RSIMeanReversion(window, lower, upper)."""
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("rsi", window),
                cmp="<",
                rhs=lower,
            ),
            short=Comparison(
                lhs=_ind("rsi", window),
                cmp=">",
                rhs=upper,
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


def donchian_breakout_seed(window: int = 20) -> StrategySpec:
    """Donchian breakout — equivalent to DonchianBreakout(window).

    Compares current close vs. the prior-window channel. The
    `donchian_high`/`donchian_low` indicators apply .shift(1) internally so
    the channel excludes the current bar — matching the existing class.
    """
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("close"),
                cmp=">",
                rhs=_ind("donchian_high", window),
            ),
            short=Comparison(
                lhs=_ind("close"),
                cmp="<",
                rhs=_ind("donchian_low", window),
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


# ──────────────────────────────────────────────────────────────────────
# v0.4.0 expansion seeds
# ──────────────────────────────────────────────────────────────────────


def macd_crossover_seed(
    n_fast: int = 12, n_slow: int = 26, n_signal: int = 9
) -> StrategySpec:
    """MACD line/signal crossover — the canonical MACD trend-following
    entry. Long on macd_line crossing above macd_signal, short on
    crossing below. Default 12/26/9 is the industry-standard parameter
    triplet.

    Brings macd_line (2-arg) and macd_signal (3-arg) into gen 0 —
    macd_signal is the only 3-arg indicator in the catalog, so without
    this seed the GA's arity-constrained swap mutation has no parent in
    the 3-arg bucket and macd_signal could only emerge via LLM
    injection.
    """
    if n_fast >= n_slow:
        raise ValueError(f"n_fast ({n_fast}) must be < n_slow ({n_slow})")
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("macd_line", n_fast, n_slow),
                cmp="crosses_above",
                rhs=_ind("macd_signal", n_fast, n_slow, n_signal),
            ),
            short=Comparison(
                lhs=_ind("macd_line", n_fast, n_slow),
                cmp="crosses_below",
                rhs=_ind("macd_signal", n_fast, n_slow, n_signal),
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


def stoch_mean_reversion_seed(
    n: int = 14, k_smooth: int = 3, lower: float = 20.0, upper: float = 80.0
) -> StrategySpec:
    """Stochastic %K mean-reversion. Long when oversold (%K < 20),
    short when overbought (%K > 80). Textbook stochastic strategy with
    standard 14/3 lookback/smoothing.

    Brings stoch (2-arg) into gen 0, adding a parent in the 2-arg
    arity bucket so the GA can mutate-swap between bb_upper / bb_lower
    / macd_line / stoch within that bucket.
    """
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("stoch", n, k_smooth),
                cmp="<",
                rhs=lower,
            ),
            short=Comparison(
                lhs=_ind("stoch", n, k_smooth),
                cmp=">",
                rhs=upper,
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


def bb_mean_reversion_seed(n: int = 20, k: float = 2.0) -> StrategySpec:
    """Bollinger band mean-reversion. Long when price closes below the
    lower band (oversold), short when above the upper band (overbought).
    Standard 20-bar SMA ± 2σ envelope.

    Brings bb_upper / bb_lower (both 2-arg) into gen 0 — these were
    already in the grammar but not seeded, so previously the BB family
    was reachable only via LLM injection. Also gives the 2-arg arity
    bucket a second parent (alongside the new stoch and macd_line seeds).
    """
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("close"),
                cmp="<",
                rhs=_ind("bb_lower", n, k),
            ),
            short=Comparison(
                lhs=_ind("close"),
                cmp=">",
                rhs=_ind("bb_upper", n, k),
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


def ichimoku_cross_seed(n_tenkan: int = 9, n_kijun: int = 26) -> StrategySpec:
    """Ichimoku tenkan/kijun crossover — the classic Ichimoku entry
    signal (often called the TK cross). Long on tenkan crossing above
    kijun, short on crossing below. Default 9/26 matches the standard
    Ichimoku setup.

    Brings ichimoku_tenkan and ichimoku_kijun (both 1-arg) into gen 0.
    Both belong to the channel indicator family — same family as
    donchian — so the same-mechanical-event grammar check allows
    long=crosses_above + short=crosses_below (opposite directions).
    """
    if n_tenkan >= n_kijun:
        raise ValueError(
            f"n_tenkan ({n_tenkan}) must be < n_kijun ({n_kijun}) "
            "— tenkan is the faster of the two lines."
        )
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("ichimoku_tenkan", n_tenkan),
                cmp="crosses_above",
                rhs=_ind("ichimoku_kijun", n_kijun),
            ),
            short=Comparison(
                lhs=_ind("ichimoku_tenkan", n_tenkan),
                cmp="crosses_below",
                rhs=_ind("ichimoku_kijun", n_kijun),
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


def adx_threshold_seed(
    n: int = 14, trend_threshold: float = 25.0, no_trend_threshold: float = 20.0
) -> StrategySpec:
    """ADX threshold — a deliberately weak strategy that exists
    primarily as a mutation-lineage parent for ADX.

    ADX measures trend STRENGTH, not direction, so an ADX-only entry
    has no inherent long/short bias — long-on-trending vs
    short-on-quiet is somewhat arbitrary. The seed isn't expected to
    score well; its value is giving the GA a starting point that
    references adx so subsequent mutations (window perturbation,
    swap to another 1-arg oscillator, crossover with a directional
    indicator) can refine it into something useful.

    The thresholds default to the textbook ADX values (25 = strong
    trend, 20 = no trend) with a hysteresis gap to avoid identical
    long/short conditions.
    """
    if no_trend_threshold >= trend_threshold:
        raise ValueError(
            f"no_trend_threshold ({no_trend_threshold}) must be < "
            f"trend_threshold ({trend_threshold}) to keep long and short "
            "conditions distinct."
        )
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("adx", n),
                cmp=">",
                rhs=trend_threshold,
            ),
            short=Comparison(
                lhs=_ind("adx", n),
                cmp="<",
                rhs=no_trend_threshold,
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


def obv_directional_seed(threshold: float = 0.0) -> StrategySpec:
    """OBV directional — a deliberately weak strategy that exists
    primarily as a mutation-lineage parent for OBV.

    OBV (On-Balance Volume) is a cumulative volume signal — its
    absolute value depends on where the series starts, so a fixed
    threshold isn't a great trade trigger. The seed long-on-positive /
    short-on-negative is the simplest possible OBV strategy.
    Like the ADX seed, the goal is giving the GA an OBV-bearing parent
    in the 0-arg arity bucket (only `close` and `obv` live there),
    not winning the backtest standalone.
    """
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(
                lhs=_ind("obv"),
                cmp=">",
                rhs=threshold,
            ),
            short=Comparison(
                lhs=_ind("obv"),
                cmp="<",
                rhs=threshold,
            ),
        ),
        exit=_PERMISSIVE_EXIT,
        size=_DEFAULT_SIZE,
    )


# Convenience: the canonical seeds with default parameters.
# ── classic parity seeds (v0.3.0) ─────────────────────────────────
MA_CROSSOVER_SEED: StrategySpec = ma_crossover_seed()
RSI_MEAN_REVERSION_SEED: StrategySpec = rsi_mean_reversion_seed()
DONCHIAN_BREAKOUT_SEED: StrategySpec = donchian_breakout_seed()
# ── v0.4.0 expansion seeds ────────────────────────────────────────
MACD_CROSSOVER_SEED: StrategySpec = macd_crossover_seed()
STOCH_MEAN_REVERSION_SEED: StrategySpec = stoch_mean_reversion_seed()
BB_MEAN_REVERSION_SEED: StrategySpec = bb_mean_reversion_seed()
ICHIMOKU_CROSS_SEED: StrategySpec = ichimoku_cross_seed()
ADX_THRESHOLD_SEED: StrategySpec = adx_threshold_seed()
OBV_DIRECTIONAL_SEED: StrategySpec = obv_directional_seed()


# Ordered by archetype so the gen-0 IDs (genome_gen_..._000..._008) line
# up legibly: trend-following → mean-reversion → breakout/channel →
# lineage seeds. Population size must be >= len(SEED_SPECS) (= 9); the
# orchestrator raises `InvalidPopulationSizeError` otherwise.
SEED_SPECS: list[StrategySpec] = [
    # Trend-following
    MA_CROSSOVER_SEED,
    MACD_CROSSOVER_SEED,
    # Mean-reversion
    RSI_MEAN_REVERSION_SEED,
    STOCH_MEAN_REVERSION_SEED,
    BB_MEAN_REVERSION_SEED,
    # Channel / breakout
    DONCHIAN_BREAKOUT_SEED,
    ICHIMOKU_CROSS_SEED,
    # Lineage seeds — weak standalone, included to give the GA a
    # mutation parent in arity buckets that would otherwise be empty.
    ADX_THRESHOLD_SEED,
    OBV_DIRECTIONAL_SEED,
]


__all__ = [
    "ADX_THRESHOLD_SEED",
    "BB_MEAN_REVERSION_SEED",
    "DONCHIAN_BREAKOUT_SEED",
    "ICHIMOKU_CROSS_SEED",
    "MACD_CROSSOVER_SEED",
    "MA_CROSSOVER_SEED",
    "OBV_DIRECTIONAL_SEED",
    "RSI_MEAN_REVERSION_SEED",
    "SEED_SPECS",
    "STOCH_MEAN_REVERSION_SEED",
    "adx_threshold_seed",
    "bb_mean_reversion_seed",
    "donchian_breakout_seed",
    "ichimoku_cross_seed",
    "ma_crossover_seed",
    "macd_crossover_seed",
    "obv_directional_seed",
    "rsi_mean_reversion_seed",
    "stoch_mean_reversion_seed",
]
