/**
 * GenomeBacktest — full trade-level detail for a single genome.
 *
 * The orchestrator only persists each genome's fitness + fold summary; we
 * re-execute the backtest on demand from the genome's DSL spec + the run's
 * data window via `GET /evolution-runs/{id}/genomes/{id}/backtest`. The
 * result is deterministic (same DSL + same candles = same trades), so
 * the user effectively gets the same drill-in experience as for an
 * optimisation trial without bloating the DB with per-genome trade rows.
 *
 * Layout mirrors `TrialDetail.tsx`:
 *   - Hero (genome id, instrument/window, fitness, link back to genome page)
 *   - Metrics summary (Sharpe, max DD, hit rate, trade count, total pips)
 *   - Candlestick chart with trade markers (reuses `StrategyChart`)
 *   - Trades table (reuses `TradesTable`)
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Calculator,
  Coins,
  DollarSign,
  Hash,
  Loader2,
  Target,
  TrendingDown,
  TrendingUp,
  XCircle,
} from 'lucide-react';

import StrategyChart from '../components/StrategyChart';
import TradesTable from '../components/TradesTable';
import { getEvolutionRun, getGenome, getGenomeBacktest } from '../services/evolutionApi';
import { fetchCandles } from '../utils/candles';
import { buildIndicatorsFromDsl } from '../utils/dslIndicators';
import type {
  EvolutionRun,
  GenomeBacktestResult,
  GenomeBacktestWindow,
  StrategyGenome,
} from '../types/evolution';
import type {
  CandleData,
  MetricsViewMode,
  Trade,
  TradeSignal,
} from '../types/optimization';
import {
  GATE_B_THRESHOLDS,
  formatDollars,
  formatPercent,
  formatPercentMagnitude,
  formatPips,
  formatSharpe,
} from '../types/optimization';

// authHeaders lived here for the local fetchCandles — that helper
// moved to ../utils/candles. Keep this file focused on the backtest
// page's mapping helpers.

function mapTrade(t: Record<string, unknown>): Trade {
  return {
    id: (t.id as string) ?? '',
    trialId: '',
    direction: t.direction as 'long' | 'short',
    entryTimestamp: t.entry_timestamp as string,
    exitTimestamp: t.exit_timestamp as string,
    entryPrice: t.entry_price as number,
    exitPrice: t.exit_price as number,
    pips: (t.pips as number) ?? 0,
    spreadCostPips: (t.spread_cost_pips as number) ?? 0,
    slippageCostPips: (t.slippage_cost_pips as number) ?? 0,
    swapCostPips: (t.swap_cost_pips as number) ?? 0,
    netPips: (t.net_pips as number) ?? 0,
    grossPnlDollars: (t.gross_pnl_dollars as number) ?? 0,
    spreadCostDollars: (t.spread_cost_dollars as number) ?? 0,
    slippageCostDollars: (t.slippage_cost_dollars as number) ?? 0,
    swapCostDollars: (t.swap_cost_dollars as number) ?? 0,
    netPnlDollars: (t.net_pnl_dollars as number) ?? 0,
    durationMinutes: (t.duration_minutes as number) ?? 0,
    positionSize: (t.position_size as number) ?? 0,
  };
}

function mapSignal(s: Record<string, unknown>): TradeSignal {
  return {
    timestamp: s.timestamp as string,
    signal: s.signal as -1 | 0 | 1,
    signalType: (s.signal_type as TradeSignal['signalType']) ?? 'entry_long',
    price: s.price as number,
    pnl: s.pnl as number | undefined,
    indicatorValues: (s.indicator_values as Record<string, number>) ?? {},
    exitReason: (s.exit_reason as TradeSignal['exitReason']) ?? null,
  };
}

// fetchCandles moved to `../utils/candles` so the paper run detail
// page can reuse the exact same paginated-fetch semantics (Wilson's
// ask: leverage the backtest chart work + defect fixes rather than
// re-implement).

export default function GenomeBacktest() {
  const { runId, genomeId } = useParams<{ runId: string; genomeId: string }>();
  const navigate = useNavigate();

  const [backtest, setBacktest] = useState<GenomeBacktestResult | null>(null);
  // Independent training-window backtest used ONLY by the "Training vs
  // OOS" comparison block — held separately from `backtest` (which
  // tracks whichever tab is active) so the comparison's "Training"
  // column never reflects OOS data just because the user happened to
  // switch tabs. Lazily fetched: skip the round-trip when active tab
  // is already training and `backtest` itself is the right value.
  const [trainingBacktest, setTrainingBacktest] =
    useState<GenomeBacktestResult | null>(null);
  const [run, setRun] = useState<EvolutionRun | null>(null);
  // Fetched in parallel with the backtest so we can render the DSL spec's
  // indicators as chart overlays without an extra round-trip later.
  const [genome, setGenome] = useState<StrategyGenome | null>(null);
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [loading, setLoading] = useState(true);
  const [candlesLoading, setCandlesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<MetricsViewMode>('pips');
  // Which slice of the run's data window the user is viewing. Switching
  // tabs refetches the backtest endpoint with `?window=<value>` so the
  // chart, metric tiles, trades, and Gate B failure list all stay
  // consistent — never a mix of training and OOS data in the same view.
  const [activeWindow, setActiveWindow] =
    useState<GenomeBacktestWindow>('training');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!runId || !genomeId) return;
      setLoading(true);
      setError(null);
      // Wipe candles between tab switches so the chart never renders an
      // OOS trade marker on a training candle by accident while the new
      // window's candles are still in flight.
      setCandles([]);
      try {
        // Fetch the active-tab backtest, run/genome metadata, AND the
        // training-window backtest needed for the always-visible
        // overfit-check comparison. The training fetch is skipped when
        // the active tab is already training — `backtest` doubles as
        // `trainingBacktest` in that case to avoid the round-trip.
        const needsTrainingFetch = activeWindow !== 'training';
        const [bt, trainBt, runData, genomeData] = await Promise.all([
          getGenomeBacktest(runId, genomeId, activeWindow),
          needsTrainingFetch
            ? getGenomeBacktest(runId, genomeId, 'training')
            : Promise.resolve(null),
          getEvolutionRun(runId),
          getGenome(runId, genomeId),
        ]);
        if (cancelled) return;
        setBacktest(bt);
        setTrainingBacktest(needsTrainingFetch ? trainBt : bt);
        setRun(runData);
        setGenome(genomeData);
        setLoading(false);

        // Fetch candles in the background — chart can render
        // progressively as they arrive
        setCandlesLoading(true);
        try {
          const c = await fetchCandles(
            bt.instrument,
            bt.granularity,
            bt.data_range_start,
            bt.data_range_end,
          );
          if (!cancelled) setCandles(c);
        } finally {
          if (!cancelled) setCandlesLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : 'Failed to load backtest',
          );
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [runId, genomeId, activeWindow]);

  const trades = useMemo<Trade[]>(
    () => (backtest ? backtest.trades.map(mapTrade) : []),
    [backtest],
  );
  const signals = useMemo<TradeSignal[]>(
    () => (backtest ? backtest.signals.map(mapSignal) : []),
    [backtest],
  );
  // Indicator overlay lines derived from the genome's DSL — SMA/EMA on the
  // price scale, RSI on a separate oscillator scale (StrategyChart handles
  // the routing via isOscillator). Computed client-side from the candle
  // data so the chart matches the spec exactly.
  const indicators = useMemo(
    () => buildIndicatorsFromDsl(genome?.dsl_json, candles),
    [genome, candles],
  );

  // Read primary metrics safely (backtest may be null on first render).
  const sharpe = backtest?.metrics.sharpe ?? 0;
  const maxDd =
    backtest?.metrics.max_drawdown ?? backtest?.metrics.mdd ?? 0;
  const hitRate = backtest?.metrics.hit_rate ?? 0;
  const cagr = backtest?.metrics.cagr ?? 0;

  // Extended metrics derived from the trades array — same shape as
  // `optimizationApi.computeExtendedMetrics`, just computed inline here
  // so we don't import the OptimizationApi class. Plus profit factor
  // and max consecutive losses (neither comes back in the backtest
  // metrics dict; both are useful for judging strategy quality).
  //
  // IMPORTANT: this and the gateBFailures memo below must live *above*
  // any early returns in this component. React requires the same number
  // of hooks to fire on every render — moving them below `if (loading)
  // return ...` triggers the "Rendered more hooks than during the
  // previous render" runtime error the first time loading flips false.
  const ext = useMemo(() => {
    const totalPips = trades.reduce((s, t) => s + (t.pips ?? 0), 0);
    const totalSpreadCostPips = trades.reduce(
      (s, t) => s + (t.spreadCostPips ?? 0),
      0,
    );
    const totalSlippageCostPips = trades.reduce(
      (s, t) => s + (t.slippageCostPips ?? 0),
      0,
    );
    const totalSwapCostPips = trades.reduce(
      (s, t) => s + (t.swapCostPips ?? 0),
      0,
    );
    const totalCostPips =
      totalSpreadCostPips + totalSlippageCostPips + totalSwapCostPips;
    const netPips = totalPips - totalCostPips;
    const grossPnlDollars = trades.reduce(
      (s, t) => s + (t.grossPnlDollars ?? 0),
      0,
    );
    const totalSpreadCostDollars = trades.reduce(
      (s, t) => s + (t.spreadCostDollars ?? 0),
      0,
    );
    const totalSlippageCostDollars = trades.reduce(
      (s, t) => s + (t.slippageCostDollars ?? 0),
      0,
    );
    const totalSwapCostDollars = trades.reduce(
      (s, t) => s + (t.swapCostDollars ?? 0),
      0,
    );
    const netPnlDollars =
      grossPnlDollars
      - totalSpreadCostDollars
      - totalSlippageCostDollars
      - totalSwapCostDollars;

    const wins = trades.filter((t) => (t.pips ?? 0) > 0);
    const losses = trades.filter((t) => (t.pips ?? 0) < 0);
    const avgWinPips =
      wins.length > 0
        ? wins.reduce((s, t) => s + (t.pips ?? 0), 0) / wins.length
        : 0;
    const avgLossPips =
      losses.length > 0
        ? losses.reduce((s, t) => s + (t.pips ?? 0), 0) / losses.length
        : 0;
    const avgWinDollars =
      wins.length > 0
        ? wins.reduce((s, t) => s + (t.netPnlDollars ?? 0), 0) / wins.length
        : 0;
    const avgLossDollars =
      losses.length > 0
        ? losses.reduce((s, t) => s + (t.netPnlDollars ?? 0), 0) / losses.length
        : 0;

    const grossProfit = wins.reduce((s, t) => s + (t.grossPnlDollars ?? 0), 0);
    const grossLoss = Math.abs(
      losses.reduce((s, t) => s + (t.grossPnlDollars ?? 0), 0),
    );
    const profitFactor =
      grossLoss > 0
        ? grossProfit / grossLoss
        : grossProfit > 0
          ? Infinity
          : 0;

    // Net profit factor: same ratio but re-classifying trades by net
    // (post-cost) outcome, so a +2 pip gross trade that paid 1 pip in
    // spread+swap counts as a +1 win, while a +0.4 pip trade that paid
    // 1 pip becomes a -0.6 loss. This is the version a trader actually
    // cares about — "did the strategy make money after costs?"
    const netWins = trades.filter((t) => (t.netPnlDollars ?? 0) > 0);
    const netLosses = trades.filter((t) => (t.netPnlDollars ?? 0) < 0);
    const netProfit = netWins.reduce(
      (s, t) => s + (t.netPnlDollars ?? 0),
      0,
    );
    const netLossAbs = Math.abs(
      netLosses.reduce((s, t) => s + (t.netPnlDollars ?? 0), 0),
    );
    const netProfitFactor =
      netLossAbs > 0
        ? netProfit / netLossAbs
        : netProfit > 0
          ? Infinity
          : 0;

    // Max consecutive losses — longest streak of losing trades by entry order.
    const ordered = [...trades].sort(
      (a, b) =>
        new Date(a.entryTimestamp).getTime() -
        new Date(b.entryTimestamp).getTime(),
    );
    let maxConsec = 0;
    let curr = 0;
    for (const t of ordered) {
      if ((t.pips ?? 0) < 0) {
        curr += 1;
        if (curr > maxConsec) maxConsec = curr;
      } else {
        curr = 0;
      }
    }

    return {
      totalPips,
      totalSpreadCostPips,
      totalSlippageCostPips,
      totalSwapCostPips,
      totalCostPips,
      netPips,
      avgPipsPerTrade: trades.length > 0 ? totalPips / trades.length : 0,
      avgNetPipsPerTrade: trades.length > 0 ? netPips / trades.length : 0,
      grossPnlDollars,
      totalSpreadCostDollars,
      totalSlippageCostDollars,
      totalSwapCostDollars,
      netPnlDollars,
      avgPnlPerTradeDollars:
        trades.length > 0 ? netPnlDollars / trades.length : 0,
      avgWinPips,
      avgLossPips,
      avgWinDollars,
      avgLossDollars,
      profitFactor,
      netProfitFactor,
      maxConsecLosses: maxConsec,
    };
  }, [trades]);

  // Gate B failure list — same checks the validator runs on the OOS fold,
  // applied here to the training-window backtest. Surfaces the *specific*
  // thresholds breached rather than just "Gate B Fail" so the user can
  // see which dimensions need work.
  const gateBFailures = useMemo<string[]>(() => {
    const out: string[] = [];
    if (sharpe < GATE_B_THRESHOLDS.SHARPE_MIN) {
      out.push(`Sharpe ${sharpe.toFixed(2)} < ${GATE_B_THRESHOLDS.SHARPE_MIN}`);
    }
    if (maxDd > GATE_B_THRESHOLDS.MAX_DRAWDOWN) {
      out.push(
        `Max DD ${formatPercentMagnitude(maxDd)} > cap ${formatPercentMagnitude(GATE_B_THRESHOLDS.MAX_DRAWDOWN)}`,
      );
    }
    if (hitRate < GATE_B_THRESHOLDS.HIT_RATE_MIN) {
      out.push(
        `Hit Rate ${formatPercent(hitRate)} < ${formatPercent(GATE_B_THRESHOLDS.HIT_RATE_MIN)}`,
      );
    }
    if (trades.length < GATE_B_THRESHOLDS.MIN_TRADES) {
      out.push(
        `Trade Count ${trades.length} < ${GATE_B_THRESHOLDS.MIN_TRADES}`,
      );
    }
    if (cagr < GATE_B_THRESHOLDS.MIN_CAGR) {
      out.push(
        `CAGR ${formatPercent(cagr)} < ${formatPercent(GATE_B_THRESHOLDS.MIN_CAGR)}`,
      );
    }
    return out;
  }, [sharpe, maxDd, hitRate, trades.length, cagr]);
  const passesGateB = gateBFailures.length === 0;

  // The genome's validation folds already include the OOS fold computed
  // by the validator at run time. We don't need to re-run anything — just
  // surface those numbers next to the training-window numbers so the
  // user can see the divergence directly (a 60-percentage-point drop in
  // hit rate from training to OOS is the canonical overfitting signature
  // and what Gate B is designed to catch).
  const oosFold = useMemo(
    () =>
      genome?.validation_folds.find((f) => f.is_oos) ?? null,
    [genome],
  );

  // ── Early returns now that all hooks have fired ─────────────────────
  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
      </div>
    );
  }

  if (error || !backtest) {
    return (
      <div className="container mx-auto px-4 py-8 space-y-4">
        <div className="px-4 py-3 rounded-md bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800 text-sm text-rose-700 dark:text-rose-300">
          {error ?? 'Backtest result not available'}
        </div>
        <button
          onClick={() => navigate(`/evolution/${runId}/genomes/${genomeId}`)}
          className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to genome
        </button>
      </div>
    );
  }

  return (
    <div className="container mx-auto px-4 py-8 space-y-5">
      <Link
        to={`/evolution/${runId}/genomes/${genomeId}`}
        className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to genome
      </Link>

      {/* Hero */}
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
        <div className="flex items-start justify-between gap-3 mb-3 flex-wrap">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide mb-1">
              <BarChart3 className="w-3.5 h-3.5" />
              Genome Backtest
            </div>
            <h1 className="font-mono text-base text-slate-900 dark:text-slate-100 break-all">
              {genomeId}
            </h1>
            <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
              Backtested on{' '}
              <span className="font-semibold text-slate-700 dark:text-slate-300">
                {backtest.instrument} {backtest.granularity}
              </span>{' '}
              from{' '}
              <span className="font-mono">
                {backtest.data_range_start.slice(0, 10)}
              </span>{' '}
              to{' '}
              <span className="font-mono">
                {backtest.data_range_end.slice(0, 10)}
              </span>
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <WindowBadge window={backtest.window} />
              {backtest.sizing && (
                <span
                  className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-violet-100 dark:bg-violet-900/40 text-violet-800 dark:text-violet-300 text-xs font-semibold"
                  title={
                    backtest.sizing.is_dsl_risk_sized
                      ? "Each trade is sized to risk the DSL's risk_pct % of account on its stop. Notional capped at leverage × balance. Position size compounds with realised P&L — wins grow the next trade, losses shrink it."
                      : 'Fixed position size from the run config (classic strategy path).'
                  }
                >
                  ${backtest.sizing.account_balance.toLocaleString()} {backtest.sizing.account_currency}
                  {' · '}
                  {backtest.sizing.leverage}:1
                  {backtest.sizing.is_dsl_risk_sized && ' · risk-sized'}
                  {backtest.sizing.compounded && ' · compounded'}
                  {backtest.sizing.leverage_cap_hits > 0 && (
                    <span className="ml-1 text-amber-700 dark:text-amber-300">
                      ⚠ {backtest.sizing.leverage_cap_hits} cap hits
                    </span>
                  )}
                </span>
              )}
              {backtest.sizing?.starting_balance != null &&
                backtest.sizing.ending_balance != null && (
                  (() => {
                    const start = backtest.sizing.starting_balance!;
                    const end = backtest.sizing.ending_balance!;
                    const peak = backtest.sizing.peak_balance ?? end;
                    const growth = start > 0 ? (end - start) / start : 0;
                    const up = end >= start;
                    const toneCls = up
                      ? 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-800 dark:text-emerald-300'
                      : 'bg-rose-100 dark:bg-rose-900/40 text-rose-800 dark:text-rose-300';
                    return (
                      <span
                        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-semibold ${toneCls}`}
                        title={`Total return over the slice — what actually happened to the account. Started $${start.toLocaleString()}, ended $${end.toLocaleString(undefined, { maximumFractionDigits: 0 })}, peaked at $${peak.toLocaleString(undefined, { maximumFractionDigits: 0 })}. The CAGR tile below expresses this same return as an annualised rate. Each trade's position size was computed against the running balance.`}
                      >
                        ${start.toLocaleString()} →{' '}
                        ${end.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        {' '}
                        <span className="opacity-80">
                          ({up ? '+' : ''}
                          {(growth * 100).toFixed(1)}% total)
                        </span>
                        {peak > end && (
                          <span className="ml-1 opacity-70">
                            peak ${peak.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                          </span>
                        )}
                      </span>
                    );
                  })()
                )}
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1.5 max-w-3xl">
              Switch tabs below to view this genome on a different slice
              of the data window:{' '}
              <strong>Training</strong> matches what the GA's fitness saw,{' '}
              <strong>OOS (held-out)</strong> matches what Gate B was
              judged on, <strong>Full</strong> backtests the entire
              window end-to-end. All metrics, the chart, and the trades
              table re-render together so you're never comparing
              training trades against OOS metrics by accident.
            </p>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1.5 max-w-3xl italic">
              Each tab here is <strong>one continuous backtest</strong>{' '}
              over the chosen slice — the account balance compounds
              across the whole window. The walk-forward fold table on
              the genome page runs each fold <strong>independently from
              ${backtest.sizing?.starting_balance != null
                ? backtest.sizing.starting_balance.toLocaleString()
                : '10,000'}{' '}
              starting balance</strong>, so the per-fold numbers there
              won't sum or average to the tile values below.
            </p>
          </div>
          <div className="flex items-center gap-1.5 text-xs">
            <button
              onClick={() => setViewMode('pips')}
              className={`px-2.5 py-1 rounded ${
                viewMode === 'pips'
                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 font-semibold'
                  : 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400'
              }`}
            >
              Pips
            </button>
            <button
              onClick={() => setViewMode('dollars')}
              className={`px-2.5 py-1 rounded ${
                viewMode === 'dollars'
                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 font-semibold'
                  : 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400'
              }`}
            >
              Dollars
            </button>
          </div>
        </div>

      </div>

      {/* ── Slice tabs (Training | OOS | Full) ──
        Switching here refetches the backtest endpoint with the new
        `?window=…` value and refreshes EVERY downstream component
        together — metrics, chart, trades, Gate B failures. The user
        cannot end up looking at OOS metrics with a training chart, or
        vice versa. The Training-vs-OOS comparison section below the
        tiles stays the same regardless of which tab is open, since
        it's an inherently between-windows view. */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <SliceTabs
          active={activeWindow}
          onChange={setActiveWindow}
          disabled={loading}
        />
        <span className="text-xs text-slate-500 dark:text-slate-400 italic">
          Side-by-side comparison appears below the tiles.
        </span>
      </div>

      {/* Section header for the metric tiles — describes the slice the
        user picked above so a reader scrolling straight to a tile knows
        what they're looking at. */}
      <div className="flex items-center justify-between gap-3 -mb-2">
        <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
          <WindowBadge window={backtest.window} compact />
          {sectionHeading(backtest.window)}
        </h3>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        <MetricCard
          icon={<TrendingUp className="w-4 h-4 text-slate-400" />}
          label="Sharpe"
          value={formatSharpe(sharpe)}
          tone={sharpe >= GATE_B_THRESHOLDS.SHARPE_MIN ? 'good' : 'bad'}
          thresholdLine={`Threshold: >= ${GATE_B_THRESHOLDS.SHARPE_MIN}`}
        />
        <MetricCard
          icon={
            viewMode === 'pips' ? (
              <Coins className="w-4 h-4 text-slate-400" />
            ) : (
              <DollarSign className="w-4 h-4 text-slate-400" />
            )
          }
          label="Net P&L (window total)"
          value={
            viewMode === 'pips'
              ? `${formatPips(ext.netPips)} pips`
              : formatDollars(ext.netPnlDollars)
          }
          tone={
            (viewMode === 'pips' ? ext.netPips : ext.netPnlDollars) >= 0
              ? 'good'
              : 'bad'
          }
          thresholdLine={
            viewMode === 'pips'
              ? `Gross ${formatPips(ext.totalPips)} − costs ${formatPips(
                  ext.totalCostPips,
                )} pips`
              : `Gross ${formatDollars(
                  ext.grossPnlDollars,
                )} − costs ${formatDollars(
                  ext.totalSpreadCostDollars + ext.totalSwapCostDollars,
                )}`
          }
        />
        <MetricCard
          icon={<TrendingDown className="w-4 h-4 text-slate-400" />}
          label="Max DD"
          value={formatPercentMagnitude(maxDd)}
          tone={maxDd <= GATE_B_THRESHOLDS.MAX_DRAWDOWN ? 'good' : 'bad'}
          thresholdLine={`Cap: ${formatPercentMagnitude(GATE_B_THRESHOLDS.MAX_DRAWDOWN)}`}
        />
        <MetricCard
          icon={<Target className="w-4 h-4 text-slate-400" />}
          label="Hit Rate"
          value={formatPercent(hitRate)}
          tone={hitRate >= GATE_B_THRESHOLDS.HIT_RATE_MIN ? 'good' : 'bad'}
          thresholdLine={`Threshold: >= ${formatPercent(GATE_B_THRESHOLDS.HIT_RATE_MIN)}`}
        />
        <MetricCard
          icon={<Hash className="w-4 h-4 text-slate-400" />}
          label="Trades"
          value={String(trades.length)}
          tone={trades.length >= GATE_B_THRESHOLDS.MIN_TRADES ? 'good' : 'bad'}
          thresholdLine={`Threshold: >= ${GATE_B_THRESHOLDS.MIN_TRADES}`}
        />
        <MetricCard
          icon={<BarChart3 className="w-4 h-4 text-slate-400" />}
          label="Profit Factor"
          value={
            Number.isFinite(ext.profitFactor)
              ? ext.profitFactor.toFixed(2)
              : '∞'
          }
          tone={
            ext.netProfitFactor >= 1.5
              ? 'good'
              : ext.netProfitFactor >= 1
                ? 'neutral'
                : 'bad'
          }
          thresholdLine={`Gross — net = ${
            Number.isFinite(ext.netProfitFactor)
              ? ext.netProfitFactor.toFixed(2)
              : '∞'
          }`}
        />
        <MetricCard
          icon={<TrendingUp className="w-4 h-4 text-slate-400" />}
          label={`CAGR (${WINDOW_STYLE[backtest.window].label.toLowerCase()}, annualised)`}
          value={formatPercent(cagr)}
          tone={cagr >= GATE_B_THRESHOLDS.MIN_CAGR ? 'good' : 'bad'}
          thresholdLine={
            backtest.window === 'oos'
              ? `Annualised rate, net of costs. Floor: >= ${formatPercent(GATE_B_THRESHOLDS.MIN_CAGR)} — this is the figure that gates promotion`
              : `Annualised rate, net of costs. Total return for the window is in the hero above. Floor: >= ${formatPercent(GATE_B_THRESHOLDS.MIN_CAGR)} (OOS gates promotion)`
          }
        />
      </div>

      {/* ── Cost breakdown strip ── */}
      {/*
        Makes the gross→net journey explicit. Sharpe and Max DD are
        computed on net returns (post-spread/swap) while Total/Profit
        Factor tiles historically displayed gross — surfacing the cost
        bite here prevents the "+2000 pips but Sharpe -11" confusion.
      */}
      <section className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Cost breakdown
          </h3>
          <span className="text-xs text-slate-500 dark:text-slate-400">
            Gross → Spread → Slippage → Swap → Net
          </span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <BreakdownItem
            label="Gross P&L"
            dollars={ext.grossPnlDollars}
            pips={ext.totalPips}
            tone={ext.totalPips >= 0 ? 'good' : 'bad'}
          />
          <BreakdownItem
            label="Spread cost"
            dollars={-Math.abs(ext.totalSpreadCostDollars)}
            pips={-Math.abs(ext.totalSpreadCostPips)}
            tone="bad"
          />
          <BreakdownItem
            label="Slippage cost"
            dollars={-Math.abs(ext.totalSlippageCostDollars)}
            pips={-Math.abs(ext.totalSlippageCostPips)}
            tone="bad"
          />
          <BreakdownItem
            label="Swap cost"
            dollars={-Math.abs(ext.totalSwapCostDollars)}
            pips={-Math.abs(ext.totalSwapCostPips)}
            tone="bad"
          />
          <BreakdownItem
            label="Net P&L"
            dollars={ext.netPnlDollars}
            pips={ext.netPips}
            tone={ext.netPips >= 0 ? 'good' : 'bad'}
            bold
          />
        </div>
      </section>

      {/* ── Training vs OOS side-by-side ── */}
      {oosFold && (
        <section className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
          <div className="flex items-start justify-between gap-3 mb-3 flex-wrap">
            <div>
              <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Training vs OOS — overfit check
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 max-w-2xl">
                Big gaps mean the strategy memorised the training data and
                won't generalise. Gate B is evaluated on the OOS column —
                that's what gates promotion to paper/live trading. If
                training and OOS are far apart, the strategy isn't trustworthy
                regardless of how good the training numbers look.
              </p>
            </div>
            <div className="text-xs text-slate-500 dark:text-slate-400 inline-flex items-center gap-2 shrink-0">
              <span className="inline-flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
                Training
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                OOS (fold {oosFold.fold_index})
              </span>
            </div>
          </div>
          {(() => {
            // Source the "Training" column from the dedicated training
            // backtest, never from `backtest` (which tracks the active
            // tab) — otherwise OOS-tab viewers would see "Training =
            // OOS" with both columns showing identical numbers.
            const tMetrics = trainingBacktest?.metrics ?? {};
            const tTrades = trainingBacktest?.trades.length ?? null;
            const tSharpe = (tMetrics.sharpe as number) ?? 0;
            const tMaxDd =
              (tMetrics.max_drawdown as number) ??
              (tMetrics.mdd as number) ??
              0;
            const tHitRate = (tMetrics.hit_rate as number) ?? 0;
            const tCagr = (tMetrics.cagr as number) ?? 0;
            return (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-3 text-sm">
                <ComparePair
                  label="Sharpe"
                  training={tSharpe}
                  oos={oosFold.sharpe}
                  format={(v) => formatSharpe(v)}
                  threshold={GATE_B_THRESHOLDS.SHARPE_MIN}
                  direction="gte"
                />
                <ComparePair
                  label="Max DD"
                  training={tMaxDd}
                  oos={oosFold.max_drawdown}
                  format={(v) => formatPercentMagnitude(v ?? 0)}
                  threshold={GATE_B_THRESHOLDS.MAX_DRAWDOWN}
                  direction="lte"
                />
                <ComparePair
                  label="Hit Rate"
                  training={tHitRate}
                  oos={oosFold.hit_rate}
                  format={(v) => formatPercent(v ?? 0)}
                  threshold={GATE_B_THRESHOLDS.HIT_RATE_MIN}
                  direction="gte"
                />
                <ComparePair
                  label="Trades"
                  training={tTrades ?? 0}
                  oos={oosFold.n_trades}
                  format={(v) => String(v ?? 0)}
                  threshold={GATE_B_THRESHOLDS.MIN_TRADES}
                  direction="gte"
                />
                <ComparePair
                  label="CAGR"
                  training={tCagr}
                  oos={oosFold.cagr}
                  format={(v) => formatPercent(v ?? 0)}
                  threshold={GATE_B_THRESHOLDS.MIN_CAGR}
                  direction="gte"
                />
              </div>
            );
          })()}
        </section>
      )}

      {/* ── Financing Breakdown (only meaningful in $ mode) ── */}
      {viewMode === 'dollars' && (
        <section className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
          <div className="flex items-center gap-2 mb-4">
            <Calculator className="w-5 h-5 text-slate-400" />
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Financing Breakdown
            </h3>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
            <BreakdownItem
              label="Gross P&L"
              dollars={ext.grossPnlDollars}
              pips={ext.totalPips}
              tone={ext.grossPnlDollars >= 0 ? 'good' : 'bad'}
            />
            <BreakdownItem
              label="Spread Cost"
              dollars={-ext.totalSpreadCostDollars}
              pips={-ext.totalSpreadCostPips}
              tone="cost"
            />
            <BreakdownItem
              label="Swap/Interest"
              dollars={-ext.totalSwapCostDollars}
              pips={-ext.totalSwapCostPips}
              tone="cost"
            />
            <BreakdownItem
              label="Total Costs"
              dollars={-(ext.totalSpreadCostDollars + ext.totalSwapCostDollars)}
              pips={-ext.totalCostPips}
              tone="cost"
            />
            <div className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-2 -m-2">
              <BreakdownItem
                label="Net P&L"
                dollars={ext.netPnlDollars}
                pips={ext.netPips}
                tone={ext.netPnlDollars >= 0 ? 'good' : 'bad'}
                bold
              />
            </div>
          </div>
        </section>
      )}

      {/* ── Secondary metrics row ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <SecondaryMetric
          label="Avg Win (gross)"
          value={
            viewMode === 'pips'
              ? `+${ext.avgWinPips.toFixed(1)} pips`
              : formatDollars(ext.avgWinDollars)
          }
          tone="good"
        />
        <SecondaryMetric
          label="Avg Loss (gross)"
          value={
            viewMode === 'pips'
              ? `${ext.avgLossPips.toFixed(1)} pips`
              : formatDollars(ext.avgLossDollars)
          }
          tone="bad"
        />
        <SecondaryMetric
          label="Avg P&L per Trade (net)"
          value={
            viewMode === 'pips'
              ? `${formatPips(ext.avgNetPipsPerTrade)} pips`
              : formatDollars(ext.avgPnlPerTradeDollars)
          }
          tone={
            (viewMode === 'pips'
              ? ext.avgNetPipsPerTrade
              : ext.avgPnlPerTradeDollars) >= 0
              ? 'good'
              : 'bad'
          }
        />
        <SecondaryMetric
          label="Max Consec. Losses"
          value={String(ext.maxConsecLosses)}
          tone="neutral"
        />
      </div>

      {/* ── Gate B Failures (when any) ── */}
      {!passesGateB && (
        <div className="rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 p-4">
          <div className="flex items-start gap-3">
            <XCircle className="w-5 h-5 text-rose-600 dark:text-rose-400 mt-0.5 shrink-0" />
            <div>
              <h3 className="font-medium text-rose-800 dark:text-rose-300">
                Gate B Validation Failed
              </h3>
              <ul className="mt-2 space-y-1">
                {gateBFailures.map((failure, idx) => (
                  <li
                    key={idx}
                    className="text-sm text-rose-700 dark:text-rose-400"
                  >
                    {failure}
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-rose-600/80 dark:text-rose-400/80 italic">
                Evaluated on the{' '}
                <strong>
                  {WINDOW_STYLE[backtest.window].label.toLowerCase()}
                </strong>{' '}
                slice currently selected. Gate B's authoritative verdict
                is the OOS row in the comparison table — switch to the
                OOS tab to see the trades behind it.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Chart */}
      <section className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
        <div className="flex items-center justify-between mb-2 gap-3 flex-wrap">
          <div>
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Price + trade markers
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              {backtest.granularity} candles — backtest's native timeframe.
              Default view shows the last ~1500 bars so individual entries +
              exits are legible. Pan/scroll the chart or hit the maximise
              icon to see the full window.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {!candlesLoading && candles.length > 0 && (
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {candles.length.toLocaleString()} bars loaded
              </span>
            )}
            {candlesLoading && (
              <span className="text-xs text-slate-500 dark:text-slate-400 inline-flex items-center gap-1">
                <Loader2 className="w-3 h-3 animate-spin" />
                loading candles…
              </span>
            )}
          </div>
        </div>
        {candlesLoading && candles.length === 0 ? (
          // Don't mount StrategyChart while candles are loading.
          // StrategyChart's isLoading branch returns a placeholder *before*
          // its chart container div is rendered, so its useRef never gets
          // populated and the init useEffect bails — the chart is never
          // created. Render our own spinner here and only mount the chart
          // once candles are present.
          <div className="flex items-center justify-center h-[500px] border border-slate-200 dark:border-slate-700 rounded-lg">
            <div className="flex flex-col items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
              <Loader2 className="w-6 h-6 animate-spin" />
              Loading {backtest.granularity} candles for the chart…
            </div>
          </div>
        ) : candles.length === 0 ? (
          <div className="text-sm text-slate-500 dark:text-slate-400 italic py-8 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 text-amber-500 shrink-0" />
            Couldn't load candles for the backtest window — the chart needs
            them but the trades + metrics above are still accurate. (The
            market-data endpoint may have rejected the date range; check
            the network tab.)
          </div>
        ) : (
          <StrategyChart
            candles={candles}
            signals={signals}
            indicators={indicators}
            instrument={backtest.instrument}
            strategy="dsl"
            params={{}}
            timeframe={backtest.granularity}
            defaultVisibleBars={1500}
          />
        )}
      </section>

      {/* Trades */}
      <section className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Trades ({trades.length})
          </h2>
        </div>
        <TradesTable
          trades={trades}
          viewMode={viewMode}
          pair={backtest.instrument}
        />
      </section>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components — mirror the trial-detail page so the two views feel
// consistent without sharing literal JSX.
// ─────────────────────────────────────────────────────────────────────

// Per-window cosmetic styling. Training is blue (matches the run-detail
// page's existing blue accent), OOS is amber (signals "this is the
// verdict — pay attention"), Full is slate (utilitarian / neutral).
const WINDOW_STYLE: Record<
  GenomeBacktestWindow,
  { label: string; chip: string; dot: string }
> = {
  training: {
    label: 'Training',
    chip: 'bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-300',
    dot: 'bg-blue-500',
  },
  oos: {
    label: 'OOS (held-out)',
    chip: 'bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-300',
    dot: 'bg-amber-500',
  },
  full: {
    label: 'Full window',
    chip: 'bg-slate-200 dark:bg-slate-700 text-slate-800 dark:text-slate-200',
    dot: 'bg-slate-500',
  },
};

const WINDOW_SUBLABEL: Record<GenomeBacktestWindow, string> = {
  training: 'folds 0..K-2 — what the GA fitness saw',
  oos: 'final fold — what Gate B was judged on',
  full: 'entire effective data window',
};

function sectionHeading(w: GenomeBacktestWindow): string {
  if (w === 'training') return 'Training-window metrics — what the GA fitness saw';
  if (w === 'oos') return 'OOS-fold metrics — Gate B verdict';
  return 'Full-window metrics — entire effective data range';
}

function WindowBadge({
  window: w,
  compact = false,
}: {
  window: GenomeBacktestWindow;
  compact?: boolean;
}) {
  const style = WINDOW_STYLE[w];
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded ${
        compact ? 'text-[10px] uppercase tracking-wide' : 'text-xs'
      } font-semibold ${style.chip}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`} />
      {compact ? style.label : `${style.label} — ${WINDOW_SUBLABEL[w]}`}
    </span>
  );
}

function SliceTabs({
  active,
  onChange,
  disabled = false,
}: {
  active: GenomeBacktestWindow;
  onChange: (w: GenomeBacktestWindow) => void;
  disabled?: boolean;
}) {
  const tabs: GenomeBacktestWindow[] = ['training', 'oos', 'full'];
  return (
    <div
      role="tablist"
      aria-label="Backtest window"
      className="inline-flex rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-0.5"
    >
      {tabs.map((w) => {
        const style = WINDOW_STYLE[w];
        const isActive = w === active;
        return (
          <button
            key={w}
            type="button"
            role="tab"
            aria-selected={isActive}
            disabled={disabled}
            onClick={() => onChange(w)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold transition-colors ${
              isActive
                ? style.chip
                : 'text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700'
            } ${disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}
            title={WINDOW_SUBLABEL[w]}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`} />
            {style.label}
          </button>
        );
      })}
    </div>
  );
}

type Tone = 'good' | 'bad' | 'neutral' | 'cost';

const TONE_CLASS: Record<Tone, string> = {
  good: 'text-emerald-600 dark:text-emerald-400',
  bad: 'text-rose-600 dark:text-rose-400',
  cost: 'text-amber-600 dark:text-amber-400',
  neutral: 'text-slate-900 dark:text-slate-100',
};

function MetricCard({
  icon,
  label,
  value,
  tone = 'neutral',
  thresholdLine,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone?: Tone;
  thresholdLine?: string;
}) {
  return (
    <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
      <div className="flex items-center gap-2 mb-2">
        {icon}
        <span className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wide">
          {label}
        </span>
      </div>
      <div className={`text-2xl font-bold font-mono ${TONE_CLASS[tone]}`}>
        {value}
      </div>
      {thresholdLine && (
        <div className="text-xs text-slate-400 mt-1">{thresholdLine}</div>
      )}
    </div>
  );
}

function BreakdownItem({
  label,
  dollars,
  pips,
  tone = 'neutral',
  bold = false,
}: {
  label: string;
  dollars: number;
  pips: number;
  tone?: Tone;
  bold?: boolean;
}) {
  return (
    <div>
      <div className="text-slate-500 dark:text-slate-400">{label}</div>
      <div
        className={`text-lg font-mono ${bold ? 'font-bold' : 'font-semibold'} ${TONE_CLASS[tone]}`}
      >
        {formatDollars(dollars)}
      </div>
      <div className="text-xs text-slate-400">{formatPips(pips)} pips</div>
    </div>
  );
}

function ComparePair({
  label,
  training,
  oos,
  format,
  threshold,
  direction,
}: {
  label: string;
  training: number | null | undefined;
  oos: number | null | undefined;
  format: (v: number | null | undefined) => string;
  threshold: number;
  direction: 'gte' | 'lte';
}) {
  const trainPasses =
    training == null
      ? null
      : direction === 'gte'
        ? training >= threshold
        : training <= threshold;
  const oosPasses =
    oos == null
      ? null
      : direction === 'gte'
        ? oos >= threshold
        : oos <= threshold;

  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">
        {label}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <div className="text-[10px] uppercase text-blue-600 dark:text-blue-400 font-semibold tracking-wide">
            Training
          </div>
          <div
            className={`font-mono text-base ${
              trainPasses === true
                ? 'text-emerald-600 dark:text-emerald-400'
                : trainPasses === false
                  ? 'text-rose-600 dark:text-rose-400'
                  : 'text-slate-900 dark:text-slate-100'
            }`}
          >
            {format(training)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-amber-600 dark:text-amber-400 font-semibold tracking-wide">
            OOS
          </div>
          <div
            className={`font-mono text-base ${
              oosPasses === true
                ? 'text-emerald-600 dark:text-emerald-400'
                : oosPasses === false
                  ? 'text-rose-600 dark:text-rose-400'
                  : 'text-slate-900 dark:text-slate-100'
            }`}
          >
            {format(oos)}
          </div>
        </div>
      </div>
    </div>
  );
}

function SecondaryMetric({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  tone?: Tone;
}) {
  return (
    <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
      <div className={`text-lg font-mono font-semibold ${TONE_CLASS[tone]}`}>
        {value}
      </div>
    </div>
  );
}
