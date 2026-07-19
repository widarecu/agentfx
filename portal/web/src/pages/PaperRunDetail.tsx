/**
 * /paper/:id run detail — the money view (US-022 / PR-J).
 *
 * Matches the wireframe's layout: sticky header with kill switch +
 * P&L, six-metric grid, LivePnLChart with dual x-axis, account +
 * risk panels. Trades table and decision-log stream are stubbed
 * with a "coming in v0.6.1" note — the backend endpoints for those
 * land in the follow-up per the plan.
 *
 * Polling: 5s while the run is not terminal. The LivePnLChart's
 * `liveSeries` is accumulated from every poll while the user is on
 * the page — matches the run's actual live-equity curve without a
 * dedicated /paper-runs/:id/equity endpoint (that's a v0.6.1 add).
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertCircle,
  ChevronLeft,
  ExternalLink,
  Loader2,
  Play,
  Trash2,
} from 'lucide-react';

import { useToast } from '../contexts/ToastContext';
import { KillSwitchButton } from '../components/KillSwitchButton';
import {
  LivePnLChart,
  type EquityPoint,
} from '../components/LivePnLChart';
import { PaperRunTradesTable } from '../components/PaperRunTradesTable';
import { PaperRunDecisionLog } from '../components/PaperRunDecisionLog';
import StrategyChart from '../components/StrategyChart';
import {
  deletePaperRun,
  getBacktestOverlayForRun,
  getDecisionsForRun,
  getEquityForRun,
  getPaperRun,
  getTradesForRun,
  killPaperRun,
  resumePaperRun,
} from '../services/paperApi';
import { getGenome } from '../services/evolutionApi';
import {
  isTerminal,
  STATUS_PILL_CLASSES,
  type BacktestOverlayResponse,
  type PaperDecisionPublic,
  type PaperRunPublic,
  type PaperRunStatus,
  type PaperTradePublic,
} from '../types/paper';
import type { CandleData, TradeSignal } from '../types/optimization';
import type { StrategyGenome } from '../types/evolution';
import { fetchLatestCandle, fetchRecentCandles } from '../utils/candles';
import { buildIndicatorsFromDsl } from '../utils/dslIndicators';
import { formatUTC } from '../utils/time';

const POLL_INTERVAL_MS = 5_000;
const CANDLES_POLL_INTERVAL_MS = 60_000; // fresh candles every minute
const CHART_BARS = 1500;
const MAX_LIVE_SAMPLES = 720; // ~1h at 5s cadence — plenty for the SVG.

export default function PaperRunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const { showToast } = useToast();

  const [run, setRun] = useState<PaperRunPublic | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const equityRef = useRef<EquityPoint[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const equitySeeded = useRef(false);

  // v0.6.1 (US-025 + US-026): Trades / decisions / backtest overlay
  // now come from real endpoints. State + polling live below; the
  // PaperRunTradesTable + PaperRunDecisionLog components render off
  // these arrays.
  const [trades, setTrades] = useState<PaperTradePublic[]>([]);
  const [decisions, setDecisions] = useState<PaperDecisionPublic[]>([]);
  const [backtestOverlay, setBacktestOverlay] =
    useState<BacktestOverlayResponse | null>(null);

  const load = useCallback(async () => {
    if (!runId) return;
    try {
      const data = await getPaperRun(runId);
      setRun(data);
      // Accumulate live equity samples if we have a balance.
      if (data.current_balance != null) {
        const point: EquityPoint = {
          timestampMs: Date.now(),
          balance: data.current_balance,
        };
        equityRef.current = [...equityRef.current, point].slice(-MAX_LIVE_SAMPLES);
        setEquity(equityRef.current);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    void load();
    const interval = setInterval(() => {
      if (run && isTerminal(run.status)) return;
      void load();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, run?.status]);

  async function handleKill() {
    if (!runId) return;
    try {
      await killPaperRun(runId);
      showToast(
        'Kill requested — the runner will halt on the next loop tick.',
        'success',
      );
      // Reload immediately so the header reflects the flag.
      void load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : String(err), 'error');
    }
  }

  async function handleResume() {
    if (!runId) return;
    try {
      await resumePaperRun(runId);
      showToast(
        'Resumed — the runner is being re-scheduled.',
        'success',
      );
      void load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : String(err), 'error');
    }
  }

  async function handleDelete() {
    if (!runId) return;
    if (
      !confirm(
        `Delete paper run ${runId}?\n\nThis removes the run row, all its ` +
          `trades, equity snapshots, and decision-log entries. The genome ` +
          `re-appears on the approval queue so you can redeploy it. Cannot ` +
          `be undone.`,
      )
    )
      return;
    try {
      await deletePaperRun(runId);
      showToast('Paper run deleted.', 'success');
      navigate('/paper');
    } catch (err) {
      showToast(err instanceof Error ? err.message : String(err), 'error');
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Candle + genome fetch for the StrategyChart section.
  //
  // Reuses `fetchRecentCandles` and `buildIndicatorsFromDsl` from the
  // backtest page (extracted to shared utils) so the indicator
  // scaling + candle shape guarantees are identical — no
  // reintroduced defects. Genome fetch happens once when the run
  // loads; candles refresh every 60s so a fresh bar rolls in as it
  // closes on the granularity boundary.
  // ─────────────────────────────────────────────────────────────

  const [genome, setGenome] = useState<StrategyGenome | null>(null);
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [candlesLoading, setCandlesLoading] = useState(false);
  // Last-known close for the open-trade unrealised P&L. Separate from
  // `candles` because `fetchRecentCandles` is windowed by wall-clock
  // and returns [] over weekends (market closed → no bars in the
  // requested 25h window). `/candles/latest` returns the newest row
  // regardless of age, so the mark-to-market display keeps working.
  // Wilson UAT 2026-07-19.
  const [latestClose, setLatestClose] = useState<number | null>(null);

  useEffect(() => {
    if (!run?.evolution_run_id || !run?.strategy_genome_id) return;
    let cancelled = false;
    void (async () => {
      try {
        const g = await getGenome(
          run.evolution_run_id!,
          run.strategy_genome_id!,
        );
        if (!cancelled) setGenome(g);
      } catch {
        // Non-fatal — chart will still render candles without
        // indicator overlays if the genome fetch fails.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [run?.evolution_run_id, run?.strategy_genome_id]);

  useEffect(() => {
    if (!run) return;
    let cancelled = false;
    const load = async () => {
      setCandlesLoading(true);
      try {
        const [windowed, latest] = await Promise.all([
          fetchRecentCandles(run.instrument, run.granularity, CHART_BARS),
          fetchLatestCandle(run.instrument, run.granularity),
        ]);
        if (!cancelled) {
          setCandles(windowed);
          setLatestClose(latest?.close ?? null);
        }
      } finally {
        if (!cancelled) setCandlesLoading(false);
      }
    };
    void load();
    // Only auto-refresh candles for non-terminal runs — a completed
    // or halted run's chart doesn't need to keep pulling new bars.
    if (run && isTerminal(run.status)) {
      return () => {
        cancelled = true;
      };
    }
    const interval = setInterval(load, CANDLES_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [run?.instrument, run?.granularity, run?.status]);

  const indicators = useMemo(
    () => buildIndicatorsFromDsl(genome?.dsl_json ?? null, candles),
    [genome, candles],
  );

  // ─────────────────────────────────────────────────────────────
  // US-025 read-endpoint wiring (v0.6.1).
  //
  // - Equity: seeded ONCE on mount from `/equity` so a reload
  //   preserves history; subsequent polls append `current_balance`
  //   from the run row (same behaviour as v0.6.0, just no longer
  //   losing the historical window).
  // - Trades: fetched on mount + every 5s while running.
  // - Decisions: fetched with a `since` cursor so we only pull new
  //   entries after the initial page load.
  // - Backtest overlay: one-shot on mount; the OOS series doesn't
  //   change over a run's lifetime.
  // ─────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!runId || equitySeeded.current) return;
    let cancelled = false;
    void (async () => {
      try {
        const eq = await getEquityForRun(runId);
        if (cancelled) return;
        const seeded: EquityPoint[] = eq.items.map((s) => ({
          timestampMs: new Date(s.timestamp).getTime(),
          balance: s.balance,
        }));
        equityRef.current = seeded;
        setEquity(seeded);
        equitySeeded.current = true;
      } catch {
        // Non-fatal — chart still accumulates from the poll loop.
        equitySeeded.current = true;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const loadTrades = async () => {
      try {
        const resp = await getTradesForRun(runId, { limit: 100 });
        if (!cancelled) setTrades(resp.items);
      } catch {
        /* non-fatal */
      }
    };
    void loadTrades();
    if (run && isTerminal(run.status)) {
      return () => {
        cancelled = true;
      };
    }
    const interval = setInterval(loadTrades, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [runId, run?.status]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let cursor: string | undefined;
    const loadDecisions = async () => {
      try {
        const resp = await getDecisionsForRun(runId, { since: cursor });
        if (cancelled) return;
        if (resp.items.length > 0) {
          // Server returns newest-first; oldest is the last row so
          // that becomes our next-cursor floor.
          cursor = resp.items[0].timestamp;
          setDecisions((prev) => {
            const newIds = new Set(resp.items.map((d) => d.id));
            const kept = prev.filter((d) => !newIds.has(d.id));
            return [...resp.items, ...kept].slice(0, 500);
          });
        }
      } catch {
        /* non-fatal */
      }
    };
    void loadDecisions();
    if (run && isTerminal(run.status)) {
      return () => {
        cancelled = true;
      };
    }
    const interval = setInterval(loadDecisions, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [runId, run?.status]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    void (async () => {
      try {
        const resp = await getBacktestOverlayForRun(runId);
        if (!cancelled) setBacktestOverlay(resp);
      } catch {
        /* non-fatal */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  // Map each Trade row → one entry marker (+ one exit marker if closed).
  // `indicatorValues` is left empty because the runner doesn't snapshot
  // per-bar indicator values into Trade rows (would need extra DB writes
  // — a v0.7.0 follow-up if the chart tooltip needs the numeric readout).
  // `exitReason` is null because paper exits come from broker-side SL/TP
  // fills (or the runner's max_hold force-close) and aren't classified
  // into the backtest's stop/target/max_hold/opposite_flip taxonomy.
  const chartSignals: TradeSignal[] = useMemo(() => {
    const out: TradeSignal[] = [];
    for (const t of trades) {
      const isLong = t.side === 'buy';
      out.push({
        timestamp: t.entry_timestamp,
        signal: isLong ? 1 : -1,
        signalType: isLong ? 'entry_long' : 'entry_short',
        price: t.entry_price,
        indicatorValues: {},
      });
      if (t.exit_timestamp !== null && t.exit_price !== null) {
        // Map paper's exit_reason → StrategyChart's exitReason. The
        // chart's TradeSignal type accepts stop / target / max_hold /
        // opposite_flip (backtest's original taxonomy). Paper adds
        // user_kill / broker_other which the chart doesn't know about
        // — coerce those to null so the marker still renders (just
        // without a specific reason label).
        const chartReason =
          t.exit_reason === 'stop' ||
          t.exit_reason === 'target' ||
          t.exit_reason === 'max_hold' ||
          t.exit_reason === 'opposite_flip'
            ? t.exit_reason
            : null;
        out.push({
          timestamp: t.exit_timestamp,
          signal: 0,
          signalType: isLong ? 'exit_long' : 'exit_short',
          price: t.exit_price,
          pnl: t.pnl ?? undefined,
          indicatorValues: {},
          exitReason: chartReason,
        });
      }
    }
    return out;
  }, [trades]);

  if (loading && !run) {
    return (
      <div className="container mx-auto px-4 py-8 flex items-center justify-center h-40">
        <Loader2 className="w-8 h-8 text-emerald-500 animate-spin" />
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="container mx-auto px-4 py-8">
        <div
          role="alert"
          className="px-4 py-3 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-300 flex items-start gap-2"
        >
          <AlertCircle className="w-5 h-5 flex-shrink-0" />
          <div>{error ?? 'Paper run not found'}</div>
        </div>
        <Link
          to="/paper"
          className="mt-4 inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
        >
          <ChevronLeft className="w-4 h-4" />
          Back to runs
        </Link>
      </div>
    );
  }

  const snapshot = run.snapshot_balance ?? 0;
  const current = run.current_balance ?? snapshot;
  const pnl = run.pnl ?? current - snapshot;
  const pnlPct = snapshot > 0 ? (pnl / snapshot) * 100 : 0;
  const runStartMs = run.started_at
    ? new Date(run.started_at).getTime()
    : run.approved_at
      ? new Date(run.approved_at).getTime()
      : Date.now();

  return (
    <div className="container mx-auto px-4 py-8 max-w-7xl">
      <Link
        to="/paper"
        className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 dark:hover:text-slate-100 mb-4"
      >
        <ChevronLeft className="w-4 h-4" />
        Back to runs
      </Link>

      <StickyHeader
        run={run}
        pnl={pnl}
        onResume={handleResume}
        pnlPct={pnlPct}
        onKill={handleKill}
        onDelete={handleDelete}
      />

      {run.status === 'ERROR' && (
        <CrashDetailPanel
          stopReason={run.stop_reason}
          crashDetail={run.crash_detail}
          canResume
        />
      )}

      <div className="mt-6 grid grid-cols-2 md:grid-cols-6 gap-3">
        <MetricCard
          label="Live P&L $"
          value={formatSignedMoney(pnl)}
          tone={pnl >= 0 ? 'positive' : 'negative'}
        />
        <MetricCard
          label="P&L %"
          value={
            snapshot > 0
              ? `${pnl >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`
              : '—'
          }
          tone={pnl >= 0 ? 'positive' : 'negative'}
        />
        <MetricCard label="Snapshot" value={formatMoney(snapshot)} />
        <MetricCard label="Live balance" value={formatMoney(current)} />
        <MetricCard
          label="Instrument"
          value={`${run.instrument} ${run.granularity}`}
          mono
        />
        <MetricCard
          label="Status"
          value={run.status}
          statusPill={run.status}
        />
      </div>

      <div className="mt-6 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <div>
            <div className="text-sm font-semibold">
              Live vs backtest equity
            </div>
            <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Dual x-axis: relative hours (bottom) + local time (top).
              Curve seeded from persisted signal-tick snapshots, then
              5s polling appends new samples.
            </div>
          </div>
          <div className="flex gap-4 text-xs">
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 bg-emerald-500 inline-block" /> Live
            </span>
            <span className="flex items-center gap-1 text-slate-500">
              <span className="w-3 h-0.5 border-t border-dashed border-slate-400 inline-block" />
              Backtest OOS
            </span>
          </div>
        </div>
        <div className="h-64 bg-slate-50 dark:bg-slate-900 rounded border border-slate-100 dark:border-slate-700">
          <LivePnLChart
            runStartMs={runStartMs}
            snapshotBalance={snapshot || current || 10_000}
            liveSeries={equity}
            backtestSeries={(backtestOverlay?.items ?? []).map((p) => ({
              timestampMs: runStartMs + p.timestamp_relative_seconds * 1000,
              balance: p.balance,
            }))}
          />
        </div>
        {backtestOverlay?.source === 'unavailable' && (
          <div className="text-xs text-slate-500 dark:text-slate-400 italic mt-2">
            Backtest OOS overlay unavailable
            {backtestOverlay.reason === 'no_associated_evolution_run' &&
              ' — this run isn\'t linked to an evolution.'}
            {backtestOverlay.reason === 'no_oos_equity_persisted' &&
              ' — no OOS folds were persisted for the source genome.'}
            {backtestOverlay.reason === 'oos_equity_series_not_persisted' &&
              " — the OOS equity-series column isn't populated on the ValidationFold rows for this genome. That per-fold curve was planned for v0.4.0 but never landed; the overlay will remain unavailable until the schema + evolution runner both persist it."}
          </div>
        )}
      </div>

      {/* Candles + indicators — same StrategyChart the backtest page
          uses, so all the indicator-scale + candle-alignment fixes
          from that work carry over. Placed directly under the
          backtest equity chart so the price context is right there
          when reading the P&L curve. */}
      <section className="mt-6 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <div>
            <div className="text-sm font-semibold">
              Price + indicators — {run.instrument} {run.granularity}
            </div>
            <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Live candles refresh every 60s. Indicators are computed from
              the same DSL the runner executes. Entry / exit markers land
              once the /paper-runs/{`{id}`}/trades endpoint ships in v0.6.1.
            </div>
          </div>
          {candlesLoading && (
            <span className="text-xs text-slate-500 dark:text-slate-400 inline-flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" />
              refreshing…
            </span>
          )}
          {!candlesLoading && candles.length > 0 && (
            <span className="text-xs text-slate-500 dark:text-slate-400">
              {candles.length.toLocaleString()} bars loaded
            </span>
          )}
        </div>
        {candlesLoading && candles.length === 0 ? (
          <div className="flex items-center justify-center h-[500px] border border-slate-200 dark:border-slate-700 rounded-lg">
            <div className="flex flex-col items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
              <Loader2 className="w-6 h-6 animate-spin" />
              Loading {run.granularity} candles for the chart…
            </div>
          </div>
        ) : candles.length === 0 ? (
          <div className="text-sm text-slate-500 dark:text-slate-400 italic py-8">
            No candles returned for {run.instrument} {run.granularity} —
            check that the ingestion worker has backfilled this
            instrument/timeframe.
          </div>
        ) : (
          <StrategyChart
            candles={candles}
            signals={chartSignals}
            indicators={indicators}
            instrument={run.instrument}
            strategy="dsl"
            params={{}}
            timeframe={run.granularity}
            defaultVisibleBars={1500}
          />
        )}
      </section>

      <div className="mt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
        <AccountRiskPanel run={run} />
        <RunLifecyclePanel run={run} />
        <RiskConfigPanel run={run} />
      </div>

      <div className="mt-6 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
        <div className="text-sm font-semibold mb-2">
          Trades{trades.length > 0 && ` (${trades.length})`}
        </div>
        <PaperRunTradesTable
          trades={trades}
          latestClose={
            latestClose ??
            (candles.length > 0 ? candles[candles.length - 1].close : null)
          }
          instrument={run.instrument}
        />
      </div>

      <div className="mt-6 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
        <div className="text-sm font-semibold mb-2">Decision log</div>
        <PaperRunDecisionLog decisions={decisions} />
      </div>
    </div>
  );
}

function StickyHeader({
  run,
  pnl,
  pnlPct,
  onKill,
  onResume,
  onDelete,
}: {
  run: PaperRunPublic;
  pnl: number;
  pnlPct: number;
  onKill: () => Promise<void>;
  onResume: () => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const disabledReason = useMemo(() => {
    if (run.kill_requested_at) return 'Kill already requested';
    if (isTerminal(run.status)) return `Run is ${run.status.toLowerCase()}`;
    return null;
  }, [run.kill_requested_at, run.status]);

  // HALTED_MANUAL always resumable (user pressed kill; recovery is
  // trivially safe). ERROR resumable too since v0.6.1 UAT feedback —
  // Wilson noted laptop hibernation crashes are typically transient
  // and forcing a re-approve is unnecessary friction. Non-recoverable
  // causes (credential decrypt, invalid DSL) will simply crash the
  // next launch the same way, at which point the fresh `crash_detail`
  // guides the next decision. COMPLETED runs aren't resumable — the
  // user would redeploy.
  const canResume = run.status === 'HALTED_MANUAL' || run.status === 'ERROR';

  const pnlColor =
    pnl >= 0
      ? 'text-emerald-700 dark:text-emerald-400'
      : 'text-rose-700 dark:text-rose-400';

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5 flex items-center justify-between sticky top-4 z-10 shadow-sm flex-wrap gap-4">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <StatusPill status={run.status} />
          <div className="text-lg font-semibold truncate">
            {run.instrument} {run.granularity}
          </div>
        </div>
        <div className="text-xs text-slate-500 dark:text-slate-400 mt-1 font-mono flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="truncate">{run.id}</span>
          {run.oanda_account_id && (
            <>
              <span className="text-slate-300 dark:text-slate-600">·</span>
              <span className="inline-flex items-center gap-1 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200 px-1.5 py-0.5 rounded normal-case">
                OANDA {run.oanda_account_id}
              </span>
            </>
          )}
        </div>
        {run.strategy_genome_id && (
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-1 font-mono flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="uppercase tracking-wide text-[10px] text-slate-400 normal-case">
              Strategy:
            </span>
            <span className="truncate">{run.strategy_genome_id}</span>
            {run.evolution_run_id && (
              <Link
                to={`/evolution/${run.evolution_run_id}/genomes/${run.strategy_genome_id}`}
                className="inline-flex items-center gap-1 text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 normal-case"
                title="Inspect the DSL, entry conditions, and walk-forward metrics for this genome"
              >
                <ExternalLink className="w-3 h-3" />
                View strategy
              </Link>
            )}
          </div>
        )}
      </div>
      <div className="flex items-center gap-6 shrink-0">
        <div className="text-right">
          <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Live P&amp;L
          </div>
          <div className={`text-2xl font-bold font-mono ${pnlColor}`}>
            {formatSignedMoney(pnl)}
          </div>
          <div className={`text-xs font-mono ${pnlColor}`}>
            {pnl >= 0 ? '+' : ''}
            {pnlPct.toFixed(2)}%
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {canResume ? (
            <ResumeButton onResume={onResume} />
          ) : (
            <KillSwitchButton onKill={onKill} disabledReason={disabledReason} />
          )}
          {isTerminal(run.status) && <DeleteButton onDelete={onDelete} />}
        </div>
      </div>
    </div>
  );
}

function DeleteButton({ onDelete }: { onDelete: () => Promise<void> }) {
  const [submitting, setSubmitting] = useState(false);
  async function handleClick() {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onDelete();
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={submitting}
      className="inline-flex items-center gap-2 px-4 py-3 bg-white dark:bg-slate-800 border border-rose-300 dark:border-rose-800 text-rose-700 dark:text-rose-300 hover:bg-rose-50 dark:hover:bg-rose-900/30 rounded-lg font-semibold disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
      title="Delete this run — removes the row, trades, decisions. Returns the genome to the approval queue."
    >
      {submitting ? (
        <Loader2 className="w-4 h-4 animate-spin" />
      ) : (
        <Trash2 className="w-4 h-4" />
      )}
      Delete
    </button>
  );
}

function ResumeButton({ onResume }: { onResume: () => Promise<void> }) {
  const [submitting, setSubmitting] = useState(false);
  async function handleClick() {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onResume();
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={submitting}
      className="inline-flex items-center gap-2 px-5 py-3 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg font-bold shadow-md disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
      title="Un-halt this run — clears the kill flag and re-schedules the runner subprocess"
    >
      {submitting ? (
        <Loader2 className="w-4 h-4 animate-spin" />
      ) : (
        <Play className="w-4 h-4" />
      )}
      Resume
    </button>
  );
}

function StatusPill({ status }: { status: PaperRunStatus }) {
  const cls = STATUS_PILL_CLASSES[status] ?? '';
  const isLive = status === 'RUNNING';
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-0.5 rounded-full ${cls}`}
    >
      {isLive && (
        <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse" />
      )}
      {status}
    </span>
  );
}

/**
 * Diagnostic panel shown when status = ERROR. Wilson's UAT feedback
 * (2026-07-13): the runner crashed with a generic
 * "process_crashed (exit_code=1)" and no way to see WHY without SSH
 * into the container. Now the child subprocess persists a structured
 * traceback to `run.parameters.crash_detail` before re-raising, and
 * the router surfaces it out here for the UI.
 *
 * `canResume` mirrors the sticky header's Resume button — the panel
 * echoes the affordance so the user doesn't have to scroll up to
 * find it after reading the trace.
 */
function CrashDetailPanel({
  stopReason,
  crashDetail,
  canResume,
}: {
  stopReason: string | null;
  crashDetail: PaperRunPublic['crash_detail'];
  canResume: boolean;
}) {
  return (
    <div className="mt-6 bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-800 rounded-lg p-5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-rose-600 dark:text-rose-400 shrink-0" />
            <div className="text-sm font-semibold text-rose-900 dark:text-rose-100">
              Runner crashed
            </div>
          </div>
          {stopReason && (
            <div className="text-xs text-rose-800 dark:text-rose-300 mt-1 font-mono">
              stop_reason: {stopReason}
            </div>
          )}
          {crashDetail?.crashed_at && (
            <div className="text-xs text-rose-800 dark:text-rose-300 mt-0.5">
              crashed at {formatUTC(crashDetail.crashed_at)}
            </div>
          )}
        </div>
        {canResume && (
          <div className="text-xs text-rose-800 dark:text-rose-300 shrink-0">
            Use the <span className="font-semibold">Resume</span> button
            above once you've read the trace. Non-recoverable causes will
            crash again with a fresh detail.
          </div>
        )}
      </div>

      {stopReason === 'OANDA_AUTH_LOST' && (
        <div className="mt-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded p-3">
          <div className="text-sm font-semibold text-amber-900 dark:text-amber-200">
            OANDA authorization was lost
          </div>
          <div className="text-xs text-amber-800 dark:text-amber-300 mt-1">
            The runner tried a fresh session with the stored token and
            still got 401. Head to{' '}
            <Link
              to="/settings"
              className="underline font-medium"
            >
              Settings → OANDA Credentials
            </Link>{' '}
            and re-verify. Once verified, the Resume button will
            re-schedule this run with the fresh credentials.
          </div>
        </div>
      )}

      {crashDetail?.exception_type && crashDetail?.message && (
        <div className="mt-3 text-sm text-rose-900 dark:text-rose-100 font-mono">
          <span className="font-semibold">{crashDetail.exception_type}:</span>{' '}
          {crashDetail.message}
        </div>
      )}

      {crashDetail?.traceback && (
        <details className="mt-3">
          <summary className="text-xs text-rose-800 dark:text-rose-300 cursor-pointer hover:underline">
            Show traceback ({crashDetail.traceback.split('\n').length} lines)
          </summary>
          <pre className="mt-2 text-xs bg-white dark:bg-slate-900 border border-rose-200 dark:border-rose-800 rounded p-3 overflow-x-auto font-mono text-slate-800 dark:text-slate-200 max-h-96">
            {crashDetail.traceback}
          </pre>
        </details>
      )}

      {!crashDetail && (
        <div className="mt-3 text-xs text-rose-800 dark:text-rose-300 italic">
          No structured crash detail was captured. This usually means the
          process died at the OS level (SIGKILL, OOM, hibernation) before
          the exception handler could persist a traceback. Check the
          container logs for the last messages from{' '}
          <span className="font-mono">agentfx.services.paper_runner</span>{' '}
          and{' '}
          <span className="font-mono">agentfx.api.workers.paper_runner</span>{' '}
          around the crash timestamp.
        </div>
      )}
    </div>
  );
}

function MetricCard({
  label,
  value,
  tone,
  mono,
  statusPill,
}: {
  label: string;
  value: string;
  tone?: 'positive' | 'negative';
  mono?: boolean;
  statusPill?: PaperRunStatus;
}) {
  const colour =
    tone === 'positive'
      ? 'text-emerald-700 dark:text-emerald-400'
      : tone === 'negative'
        ? 'text-rose-700 dark:text-rose-400'
        : 'text-slate-800 dark:text-slate-100';
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </div>
      {statusPill ? (
        <div className="mt-1">
          <StatusPill status={statusPill} />
        </div>
      ) : (
        <div
          className={`text-lg font-semibold mt-1 ${colour} ${mono ? 'font-mono text-sm' : 'font-mono'}`}
        >
          {value}
        </div>
      )}
    </div>
  );
}

function AccountRiskPanel({ run }: { run: PaperRunPublic }) {
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
      <div className="text-sm font-semibold mb-3">Account &amp; balance</div>
      <dl className="space-y-2 text-sm">
        <Row label="OANDA account">
          <span className="font-mono text-xs">
            {run.oanda_account_id ?? '—'}
          </span>
        </Row>
        <Row label="Environment">
          <span className="uppercase text-xs">
            {run.oanda_environment ?? 'practice'}
          </span>
        </Row>
        <Row label="Snapshot at start">
          <span className="font-mono text-slate-500">
            {run.snapshot_balance != null
              ? formatMoney(run.snapshot_balance)
              : '—'}
          </span>
        </Row>
        <Row label="Live balance">
          <span className="font-mono">
            {run.current_balance != null
              ? formatMoney(run.current_balance)
              : '—'}
          </span>
        </Row>
      </dl>
      <div className="mt-3 text-xs text-slate-500 dark:text-slate-400">
        P&amp;L % = (balance − snapshot) / snapshot. Balance refetches from
        OANDA every 5s.
      </div>
    </div>
  );
}

function RunLifecyclePanel({ run }: { run: PaperRunPublic }) {
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
      <div className="text-sm font-semibold mb-3">Lifecycle</div>
      <dl className="space-y-2 text-sm">
        <Row label="Approved">
          <span className="font-mono text-xs">
            {run.approved_at ? formatUTC(run.approved_at) : '—'}
          </span>
        </Row>
        <Row label="Started">
          <span className="font-mono text-xs">
            {run.started_at ? formatUTC(run.started_at) : '—'}
          </span>
        </Row>
        <Row label="Kill requested">
          <span className="font-mono text-xs">
            {run.kill_requested_at ? formatUTC(run.kill_requested_at) : '—'}
          </span>
        </Row>
        <Row label="Completed">
          <span className="font-mono text-xs">
            {run.completed_at ? formatUTC(run.completed_at) : '—'}
          </span>
        </Row>
        {run.stop_reason && (
          <Row label="Stop reason">
            <span className="font-mono text-xs text-rose-700 dark:text-rose-400">
              {run.stop_reason}
            </span>
          </Row>
        )}
      </dl>
    </div>
  );
}

function RiskConfigPanel({ run }: { run: PaperRunPublic }) {
  const cfg = run.risk_config;
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
      <div className="text-sm font-semibold mb-3">Risk config</div>
      <dl className="space-y-2 text-sm">
        <Row label="Max position">
          {/* v0.6.2 (Wilson UAT 2026-07-19): field removed from the
              deploy modal — new runs land here with no
              `max_position_size_units` key. Render "dynamic" so users
              know sizing is driven by risk_pct × balance. Legacy runs
              (pre-2026-07-19) still show the stored value. */}
          <span className="font-mono">
            {cfg?.max_position_size_units != null
              ? `${cfg.max_position_size_units.toLocaleString()} units`
              : 'dynamic (risk_pct × balance)'}
          </span>
        </Row>
        <Row label="Daily loss cap">
          <span className="font-mono">
            {cfg ? `${cfg.daily_loss_pct}%` : '—'}
          </span>
        </Row>
        <Row label="Kill-switch DD">
          <span className="font-mono">
            {cfg ? `${cfg.kill_switch_dd_pct}%` : '—'}
          </span>
        </Row>
      </dl>
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex justify-between items-baseline gap-4">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

function formatMoney(v: number): string {
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSignedMoney(v: number): string {
  const abs = Math.abs(v);
  return `${v >= 0 ? '+' : '-'}$${abs.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
