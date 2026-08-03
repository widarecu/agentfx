/**
 * Mobile paper-run monitoring view (<640px).
 *
 * Answers, in order, Wilson's five monitoring questions:
 *   1. Is it still running?  (sticky status bar with 5s heartbeat)
 *   2. How's P&L today?     (hero card + realised/unrealised split)
 *   3. What's my exposure?  (current-position card + directional price scale)
 *   4. Anything to react?   (activity feed + daily-cap gauge)
 *   5. Kill switch          (sticky full-width bottom button)
 *
 * The desktop view (PaperRunDetail.tsx) stays untouched. This
 * component owns its own polling — only one of {mobile, desktop}
 * mounts at a time (see useIsMobile).
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { AlertTriangle, ChevronLeft, ChevronRight } from 'lucide-react';

import { useToast } from '../contexts/ToastContext';
import { KillSwitchButton } from '../components/KillSwitchButton';
import {
  getDecisionsForRun,
  getEquityForRun,
  getPaperRun,
  getTradesForRun,
  killPaperRun,
} from '../services/paperApi';
import { getGenome } from '../services/evolutionApi';
import {
  isTerminal,
  type PaperDecisionPublic,
  type PaperRunPublic,
  type PaperRunStatus,
  type PaperTradePublic,
  type EquitySnapshotPublic,
} from '../types/paper';
import type { StrategyGenome } from '../types/evolution';
import type { CandleData } from '../types/optimization';
import {
  fetchCandles,
  fetchLatestCandle,
  granularityToSeconds,
} from '../utils/candles';
import { formatUTC } from '../utils/time';

const POLL_INTERVAL_MS = 5_000;
const EQUITY_POLL_INTERVAL_MS = 60_000;
const PREVIEW_POLL_INTERVAL_MS = 60_000;
const HEARTBEAT_TICK_MS = 30_000;
const PREVIEW_CANDLE_COUNT = 30;
const ACTIVITY_FEED_LIMIT = 6;

// ─── SL/TP resolution ────────────────────────────────────────────────
// The runner (paper_runner.py:1291–1297) resolves SL/TP with a fallback:
//   1. run.risk_config.stop_loss_pips / take_profit_pips (deploy modal)
//   2. otherwise the genome's DSL exit rules
// Wilson UAT 2026-08-03: main view + risk sub-route were reading only
// (1); runs where SL/TP live in the DSL rendered "No SL/TP configured".
// This helper mirrors the runner so the mobile UI stays in agreement.
export function resolveSlTpPips(
  run: PaperRunPublic | null | undefined,
  genome: StrategyGenome | null | undefined,
): { slPips: number; tpPips: number; source: 'risk_config' | 'genome_dsl' | 'unknown' } {
  const rcSl = run?.risk_config?.stop_loss_pips;
  const rcTp = run?.risk_config?.take_profit_pips;
  if (rcSl != null && rcTp != null) {
    return { slPips: rcSl, tpPips: rcTp, source: 'risk_config' };
  }
  const dslExit = genome?.dsl_json?.exit ?? {};
  const dslSl = typeof dslExit.stop_pips === 'number' ? dslExit.stop_pips : null;
  const dslTp = typeof dslExit.target_pips === 'number' ? dslExit.target_pips : null;
  const slPips = rcSl ?? dslSl ?? 0;
  const tpPips = rcTp ?? dslTp ?? 0;
  const source: 'risk_config' | 'genome_dsl' | 'unknown' =
    slPips > 0 || tpPips > 0
      ? rcSl != null || rcTp != null
        ? 'risk_config'
        : 'genome_dsl'
      : 'unknown';
  return { slPips, tpPips, source };
}

// ─── pip helpers (mirrors PaperRunTradesTable's local copy — kept small
//      and duplicated intentionally). ─────────────────────────────────
export function pipSizeFor(instrument: string | null | undefined): number {
  if (!instrument) return 0.0001;
  const parts = instrument.toUpperCase().replace('/', '_').split('_');
  if (parts.length !== 2) return 0.0001;
  return parts[1] === 'JPY' ? 0.01 : 0.0001;
}

function formatUSD(n: number | null | undefined, opts?: { signed?: boolean }): string {
  if (n == null || Number.isNaN(n)) return '—';
  const abs = Math.abs(n);
  const body = abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = n < 0 ? '−' : opts?.signed ? '+' : '';
  return `${sign}$${body}`;
}

function formatPct(n: number | null | undefined, opts?: { signed?: boolean }): string {
  if (n == null || Number.isNaN(n)) return '—';
  const sign = n < 0 ? '−' : opts?.signed ? '+' : '';
  return `${sign}${Math.abs(n).toFixed(2)}%`;
}

function formatElapsed(startedAtIso: string | null): string {
  if (!startedAtIso) return '—';
  const started = new Date(startedAtIso).getTime();
  const diffMs = Math.max(0, Date.now() - started);
  const totalMinutes = Math.floor(diffMs / 60_000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    return `${days}d ${hours % 24}h`;
  }
  return `${hours}h ${String(minutes).padStart(2, '0')}m`;
}

function formatShortTime(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

function shortRunId(runId: string): string {
  return runId.length <= 12 ? runId : `run_…${runId.slice(-6)}`;
}

function relativeAge(iso: string | null): string {
  if (!iso) return '—';
  const diff = Math.max(0, Date.now() - new Date(iso).getTime());
  if (diff < 5_000) return 'just now';
  if (diff < 60_000) return `${Math.round(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m ago`;
  return `${Math.round(diff / 3_600_000)}h ago`;
}

// ─── status pill styling ─────────────────────────────────────────────
function statusPillClass(status: PaperRunStatus | undefined): string {
  if (!status) return 'text-slate-400';
  switch (status) {
    case 'RUNNING':
      return 'text-emerald-400';
    case 'HALTED_MANUAL':
    case 'COMPLETED':
    case 'PAUSED':
    case 'PENDING':
    case 'AWAITING_APPROVAL':
    case 'APPROVED':
      return 'text-slate-400';
    case 'ERROR':
      return 'text-rose-400';
    default:
      return 'text-slate-400';
  }
}

function statusLabel(status: PaperRunStatus | undefined): string {
  if (!status) return '—';
  switch (status) {
    case 'RUNNING':
      return 'Running';
    case 'HALTED_MANUAL':
      return 'Halted';
    case 'COMPLETED':
      return 'Completed';
    case 'ERROR':
      return 'Error';
    case 'PAUSED':
      return 'Paused';
    case 'PENDING':
      return 'Pending';
    case 'AWAITING_APPROVAL':
      return 'Awaiting approval';
    case 'APPROVED':
      return 'Approved';
    default:
      return status;
  }
}

// ─── derived: open positions, realised/unrealised, direction-scale ───

interface OpenPosition {
  trade: PaperTradePublic;
  unrealisedUsd: number | null;
  isLong: boolean;
}

function deriveOpenPositions(
  trades: PaperTradePublic[],
  latestClose: number | null,
  instrument: string,
): OpenPosition[] {
  const pip = pipSizeFor(instrument);
  return trades
    .filter((t) => t.exit_timestamp === null)
    .map((trade) => {
      const isLong = trade.side === 'buy';
      const sign = isLong ? 1 : -1;
      let unrealisedUsd: number | null = null;
      if (latestClose != null) {
        const pipMove = ((latestClose - trade.entry_price) * sign) / pip;
        // USD-per-pip for a size-N contract on a non-JPY pair ≈ N * pip.
        // Good enough for the mobile glance-view; the reconciled dollar
        // figure lives in the trade row once it closes.
        unrealisedUsd = pipMove * trade.quantity * pip;
      }
      return { trade, unrealisedUsd, isLong };
    });
}

function realisedTodayUsd(trades: PaperTradePublic[]): {
  total: number;
  wins: number;
  losses: number;
  closedCount: number;
} {
  const closed = trades.filter((t) => t.exit_timestamp !== null && t.pnl != null);
  const total = closed.reduce((acc, t) => acc + (t.net_pnl ?? t.pnl ?? 0), 0);
  const wins = closed.filter((t) => (t.pnl ?? 0) > 0).length;
  const losses = closed.filter((t) => (t.pnl ?? 0) < 0).length;
  return { total, wins, losses, closedCount: closed.length };
}

// Layout is ALWAYS price-low-left / price-high-right so the reading is
// physical. For LONG: SL sits on the low end (below entry), TP on the
// high end (above entry). For SHORT: TP on the low end, SL on the high
// end. entryPctFromLeft and nowPctFromLeft are 0..100 along that axis.
export function _computeScaleInner(
  trade: PaperTradePublic,
  isLong: boolean,
  latestClose: number,
  slPips: number,
  tpPips: number,
  pip: number,
) {
  const entry = trade.entry_price;
  const slPrice = isLong ? entry - slPips * pip : entry + slPips * pip;
  const tpPrice = isLong ? entry + tpPips * pip : entry - tpPips * pip;
  const min = Math.min(slPrice, tpPrice);
  const max = Math.max(slPrice, tpPrice);
  const range = max - min;
  const pctOf = (v: number) => (range === 0 ? 50 : ((v - min) / range) * 100);
  const sign = isLong ? 1 : -1;
  const pipsInFavour = ((latestClose - entry) * sign) / pip;
  return {
    slPrice,
    tpPrice,
    entryPrice: entry,
    nowPrice: latestClose,
    entryPctFromLeft: Math.max(0, Math.min(100, pctOf(entry))),
    nowPctFromLeft: Math.max(0, Math.min(100, pctOf(latestClose))),
    isLong,
    pipsInFavour,
  };
}

// ─── activity feed (interleaved trades + decisions, newest first) ────

type ActivityRow =
  | { kind: 'trade-open'; ts: string; trade: PaperTradePublic }
  | { kind: 'trade-close'; ts: string; trade: PaperTradePublic }
  | { kind: 'decision'; ts: string; decision: PaperDecisionPublic };

const NOISY_DECISION_ACTIONS = new Set([
  'USER_ORDER_PLACED',
  'USER_POSITION_CLOSED',
]);

export function buildActivity(
  trades: PaperTradePublic[],
  decisions: PaperDecisionPublic[],
  limit: number,
): ActivityRow[] {
  const rows: ActivityRow[] = [];
  for (const t of trades) {
    rows.push({ kind: 'trade-open', ts: t.entry_timestamp, trade: t });
    if (t.exit_timestamp) {
      rows.push({ kind: 'trade-close', ts: t.exit_timestamp, trade: t });
    }
  }
  for (const d of decisions) {
    // Skip the two actions redundant with trade-open/close rows.
    if (NOISY_DECISION_ACTIONS.has(d.action)) continue;
    rows.push({ kind: 'decision', ts: d.timestamp, decision: d });
  }
  rows.sort((a, b) => b.ts.localeCompare(a.ts));
  return rows.slice(0, limit);
}

// ─── sparkline path ──────────────────────────────────────────────────

function equityToSparkline(
  equity: EquitySnapshotPublic[],
  snapshotBalance: number | null,
): { linePath: string; areaPath: string; endpointX: number; endpointY: number } | null {
  if (equity.length < 2 || snapshotBalance == null) return null;
  const values = equity.map((e) => e.balance);
  const min = Math.min(snapshotBalance, ...values);
  const max = Math.max(snapshotBalance, ...values);
  const range = max - min;
  const W = 300;
  const H = 96;
  const padY = 8;
  const usableH = H - padY * 2;
  const pctX = (i: number) => (equity.length === 1 ? W : (i / (equity.length - 1)) * W);
  const pctY = (v: number) =>
    range === 0 ? H / 2 : H - padY - ((v - min) / range) * usableH;

  const points = equity.map((e, i) => ({ x: pctX(i), y: pctY(e.balance) }));
  const linePath =
    'M' + points.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' L');
  const areaPath =
    `${linePath} L${W.toFixed(2)},${H} L0,${H} Z`;
  const last = points[points.length - 1];
  return { linePath, areaPath, endpointX: last.x, endpointY: last.y };
}

// ─── page ────────────────────────────────────────────────────────────

export default function MobilePaperRunDetail(): React.ReactElement {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const { showToast } = useToast();

  const [run, setRun] = useState<PaperRunPublic | null>(null);
  const [genome, setGenome] = useState<StrategyGenome | null>(null);
  const [trades, setTrades] = useState<PaperTradePublic[]>([]);
  const [decisions, setDecisions] = useState<PaperDecisionPublic[]>([]);
  const [equity, setEquity] = useState<EquitySnapshotPublic[]>([]);
  const [previewCandles, setPreviewCandles] = useState<CandleData[]>([]);
  const [latestClose, setLatestClose] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastPolledAt, setLastPolledAt] = useState<string | null>(null);
  const [nowTick, setNowTick] = useState(Date.now());

  const loadRun = useCallback(async () => {
    if (!runId) return;
    try {
      const data = await getPaperRun(runId);
      setRun(data);
      setLastPolledAt(new Date().toISOString());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  const loadTrades = useCallback(async () => {
    if (!runId) return;
    try {
      const list = await getTradesForRun(runId, { limit: 200 });
      setTrades(list.items);
    } catch {
      /* transient — keep prior */
    }
  }, [runId]);

  const loadDecisions = useCallback(async () => {
    if (!runId) return;
    try {
      const list = await getDecisionsForRun(runId, { limit: 100 });
      setDecisions(list.items);
    } catch {
      /* transient */
    }
  }, [runId]);

  const loadEquity = useCallback(async () => {
    if (!runId) return;
    try {
      const list = await getEquityForRun(runId);
      setEquity(list.items);
    } catch {
      /* transient */
    }
  }, [runId]);

  // ── polling ──────────────────────────────────────────────────────
  useEffect(() => {
    void loadRun();
    void loadTrades();
    void loadDecisions();
    void loadEquity();
    const runInterval = setInterval(() => {
      if (run && isTerminal(run.status)) return;
      void loadRun();
      void loadTrades();
      void loadDecisions();
    }, POLL_INTERVAL_MS);
    const equityInterval = setInterval(() => {
      if (run && isTerminal(run.status)) return;
      void loadEquity();
    }, EQUITY_POLL_INTERVAL_MS);
    const tickInterval = setInterval(() => setNowTick(Date.now()), HEARTBEAT_TICK_MS);
    return () => {
      clearInterval(runInterval);
      clearInterval(equityInterval);
      clearInterval(tickInterval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadRun, loadTrades, loadDecisions, loadEquity, run?.status]);

  // ── genome (one-shot; used only for SL/TP fallback) ──────────────
  useEffect(() => {
    if (!run?.evolution_run_id || !run.strategy_genome_id) return;
    let cancelled = false;
    getGenome(run.evolution_run_id, run.strategy_genome_id)
      .then((g) => {
        if (!cancelled) setGenome(g);
      })
      .catch(() => {
        /* genome fetch is best-effort; SL/TP just fall through to "—". */
      });
    return () => {
      cancelled = true;
    };
  }, [run?.evolution_run_id, run?.strategy_genome_id]);

  // ── preview candles + latest close ───────────────────────────────
  useEffect(() => {
    if (!run) return;
    const load = async () => {
      const granularitySec = granularityToSeconds(run.granularity);
      const toTime = new Date().toISOString();
      const fromTime = new Date(
        Date.now() - granularitySec * PREVIEW_CANDLE_COUNT * 1000,
      ).toISOString();
      try {
        const [bars, latest] = await Promise.all([
          fetchCandles(run.instrument, run.granularity, fromTime, toTime),
          fetchLatestCandle(run.instrument, run.granularity),
        ]);
        setPreviewCandles(bars);
        if (latest) setLatestClose(latest.close);
      } catch {
        /* keep prior */
      }
    };
    void load();
    const interval = setInterval(load, PREVIEW_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [run?.instrument, run?.granularity]);

  // ── derived ──────────────────────────────────────────────────────
  const realised = useMemo(() => realisedTodayUsd(trades), [trades]);
  const openPositions = useMemo(
    () => (run ? deriveOpenPositions(trades, latestClose, run.instrument) : []),
    [trades, latestClose, run],
  );
  const unrealisedUsd = openPositions.reduce(
    (acc, p) => acc + (p.unrealisedUsd ?? 0),
    0,
  );
  const totalPnlUsd = (run?.pnl ?? realised.total) + unrealisedUsd;
  const pnlPct =
    run?.snapshot_balance && run.snapshot_balance !== 0
      ? (totalPnlUsd / run.snapshot_balance) * 100
      : null;

  const activity = useMemo(
    () => buildActivity(trades, decisions, ACTIVITY_FEED_LIMIT),
    [trades, decisions],
  );

  // Recomputed on the 30s tick so the "elapsed" + "N ago" strings advance
  // between polls without a full refetch.
  const elapsedText = useMemo(
    () => formatElapsed(run?.started_at ?? null),
    [run?.started_at, nowTick],
  );
  const lastPollText = useMemo(
    () => relativeAge(lastPolledAt),
    [lastPolledAt, nowTick],
  );

  const dailyLossPct = run?.risk_config?.daily_loss_pct ?? null;
  // Cap gauge: bar range is [-dailyLossPct, +dailyLossPct]; halt tick is
  // at 0% (leftmost). Marker sits at the current pnl-pct scaled onto
  // this range. Below 50%-of-cap = SAFE, 50–80% = WARNING, 80%+ = NEAR HALT.
  const capUsage =
    dailyLossPct != null && pnlPct != null
      ? Math.max(0, -pnlPct) / dailyLossPct
      : 0;
  const capLabel =
    capUsage >= 0.8 ? 'Near halt' : capUsage >= 0.5 ? 'Warning' : 'Safe';
  const capLabelClass =
    capUsage >= 0.8
      ? 'text-rose-400 animate-pulse'
      : capUsage >= 0.5
        ? 'text-amber-400'
        : 'text-emerald-400';

  const spark = useMemo(
    () => equityToSparkline(equity, run?.snapshot_balance ?? null),
    [equity, run?.snapshot_balance],
  );

  // ── actions ──────────────────────────────────────────────────────
  const handleKill = async () => {
    if (!runId) return;
    try {
      await killPaperRun(runId);
      showToast(
        'Kill requested — the runner will halt on the next loop tick.',
        'success',
      );
      void loadRun();
    } catch (err) {
      showToast(err instanceof Error ? err.message : String(err), 'error');
    }
  };

  const killDisabledReason = run?.kill_requested_at
    ? 'Halt requested'
    : run && isTerminal(run.status)
      ? statusLabel(run.status)
      : null;

  // ── render ───────────────────────────────────────────────────────
  if (loading && !run) {
    return (
      <div className="min-h-screen bg-slate-900 text-slate-100 flex items-center justify-center p-4 text-sm">
        Loading run…
      </div>
    );
  }
  if (error && !run) {
    return (
      <div className="min-h-screen bg-slate-900 text-slate-100 flex flex-col items-center justify-center gap-3 p-4">
        <AlertTriangle className="w-8 h-8 text-rose-400" />
        <div className="text-sm text-rose-400">{error}</div>
        <button
          onClick={() => navigate('/paper')}
          className="text-xs text-slate-400 underline"
        >
          Back to runs
        </button>
      </div>
    );
  }
  if (!run) return <div />;

  const primaryOpen = openPositions[0] ?? null;
  const { slPips, tpPips } = resolveSlTpPips(run, genome);
  const scale =
    primaryOpen && latestClose != null && slPips > 0 && tpPips > 0
      ? _computeScaleInner(
          primaryOpen.trade,
          primaryOpen.isLong,
          latestClose,
          slPips,
          tpPips,
          pipSizeFor(run.instrument),
        )
      : null;

  const isRunning = run.status === 'RUNNING';
  const isError = run.status === 'ERROR';
  const dotColor = isError
    ? 'bg-rose-400'
    : isRunning
      ? 'bg-emerald-400'
      : 'bg-slate-400';

  const pnlPositive = totalPnlUsd >= 0;
  const pnlColor = pnlPositive ? 'text-emerald-400' : 'text-rose-400';

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 flex flex-col relative">
      {/* Sticky status bar */}
      <header
        className="sticky top-0 z-40 flex items-center gap-2 px-3 h-12 border-b border-slate-800 backdrop-blur"
        style={{ background: 'rgba(15,23,42,0.92)' }}
      >
        <Link
          to="/paper"
          className="w-8 h-8 inline-flex items-center justify-center rounded text-slate-400"
          aria-label="Back to paper runs"
        >
          <ChevronLeft size={18} />
        </Link>
        <div className="flex flex-col min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="relative inline-flex items-center">
              <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
              {isRunning && (
                <span className="absolute inset-0 w-1.5 h-1.5 rounded-full bg-emerald-400 opacity-70 animate-ping" />
              )}
            </span>
            <span
              className={`text-[11px] font-semibold uppercase tracking-wider ${statusPillClass(run.status)}`}
            >
              {statusLabel(run.status)}
            </span>
            <span className="text-slate-600 text-[11px]">·</span>
            <span className="text-[11px] font-mono text-slate-300 truncate">
              {shortRunId(run.id)}
            </span>
          </div>
          <div className="text-[10px] text-slate-500 font-mono truncate">
            {run.instrument} · {run.granularity} · Paper ·{' '}
            {run.oanda_environment ?? 'practice'}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1 text-[10px] font-mono text-slate-500 uppercase tracking-wider">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          <span>5s</span>
        </div>
      </header>

      {/* Body */}
      <main className="flex-1 flex flex-col gap-3 p-3 pb-24">
        {/* 1. HERO P&L */}
        <section
          className="bg-slate-800 border border-slate-700 rounded-xl p-4 flex flex-col gap-3"
          aria-label="Today's P&L"
        >
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">
              Today · Live P&L
            </span>
            <span
              className="text-[10px] font-mono text-slate-500"
              title={run.started_at ?? undefined}
            >
              {elapsedText} elapsed
            </span>
          </div>
          <div className="flex items-baseline gap-2">
            <span
              className={`text-4xl font-mono font-semibold tabular-nums ${pnlColor}`}
            >
              {formatUSD(totalPnlUsd, { signed: true })}
            </span>
          </div>
          <div className="flex items-center gap-2 -mt-1">
            <span className={`text-lg font-mono tabular-nums ${pnlColor}`}>
              {formatPct(pnlPct, { signed: true })}
            </span>
            <span className="text-xs text-slate-500 font-mono">
              of {formatUSD(run.snapshot_balance)} snapshot
            </span>
          </div>

          <div className="grid grid-cols-2 gap-2 mt-1">
            <div className="bg-slate-950 border border-slate-700 rounded p-2.5">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">
                Realised
              </div>
              <div
                className={`text-base font-mono font-semibold tabular-nums mt-0.5 ${realised.total >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}
              >
                {formatUSD(realised.total, { signed: true })}
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5 font-mono">
                {realised.closedCount} closed · {realised.wins}W / {realised.losses}L
              </div>
            </div>
            <div className="bg-slate-950 border border-slate-700 rounded p-2.5">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">
                Unrealised
              </div>
              <div
                className={`text-base font-mono font-semibold tabular-nums mt-0.5 ${unrealisedUsd >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}
              >
                {openPositions.length === 0
                  ? '—'
                  : formatUSD(unrealisedUsd, { signed: true })}
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5 font-mono">
                {openPositions.length === 0
                  ? 'no open positions'
                  : openPositions.length === 1
                    ? `1 open · ${primaryOpen?.isLong ? 'LONG' : 'SHORT'}`
                    : `${openPositions.length} open`}
              </div>
            </div>
          </div>
        </section>

        {/* 2. CURRENT POSITION */}
        {primaryOpen && (
          <section
            className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden"
            aria-label="Current position"
          >
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-700">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">
                Current position
              </span>
              <span className="text-[10px] font-mono text-slate-500">
                opened {relativeAge(primaryOpen.trade.entry_timestamp)}
              </span>
            </div>
            <div className="p-4 flex flex-col gap-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold font-mono uppercase tracking-wider border ${primaryOpen.isLong ? 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10' : 'text-rose-400 border-rose-500/40 bg-rose-500/10'}`}
                >
                  {primaryOpen.isLong ? '▲' : '▼'}
                  {primaryOpen.isLong ? 'Long' : 'Short'}
                </span>
                <span className="text-sm font-mono font-semibold text-slate-100">
                  {primaryOpen.trade.quantity.toLocaleString()}
                </span>
                <span className="text-xs text-slate-500 font-mono">
                  units · {run.instrument}
                </span>
                <span
                  className={`ml-auto text-sm font-mono font-semibold tabular-nums ${(primaryOpen.unrealisedUsd ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}
                >
                  {primaryOpen.unrealisedUsd != null
                    ? formatUSD(primaryOpen.unrealisedUsd, { signed: true })
                    : '—'}
                </span>
              </div>

              {scale ? (
                <PositionScaleBar scale={scale} slPips={slPips} tpPips={tpPips} />
              ) : (
                <div className="text-[11px] font-mono text-slate-500 italic">
                  No SL/TP configured — price scale unavailable.
                </div>
              )}
            </div>
          </section>
        )}

        {/* 3. EQUITY SPARKLINE */}
        <button
          type="button"
          onClick={() => navigate(`/paper/${runId}/chart`)}
          className="bg-slate-800 border border-slate-700 rounded-xl p-4 text-left w-full active:bg-slate-700/50"
          aria-label="Equity today — tap for full chart"
        >
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">
              Equity · today
            </span>
            <span className="text-[10px] font-mono text-slate-500">
              tap to open full chart
            </span>
          </div>
          <div className="mt-3 relative" style={{ height: 96 }}>
            {spark ? (
              <>
                <svg
                  viewBox="0 0 300 96"
                  preserveAspectRatio="none"
                  width="100%"
                  height="100%"
                  role="img"
                  aria-label="Equity curve"
                >
                  <path
                    d={spark.areaPath}
                    fill={pnlPositive ? 'rgba(52,211,153,0.15)' : 'rgba(251,113,133,0.15)'}
                    stroke="none"
                  />
                  <path
                    d={spark.linePath}
                    fill="none"
                    stroke={pnlPositive ? '#34d399' : '#fb7185'}
                    strokeWidth="1.75"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <circle
                    cx={spark.endpointX}
                    cy={spark.endpointY}
                    r="4"
                    fill={pnlPositive ? '#34d399' : '#fb7185'}
                    stroke="#1e293b"
                    strokeWidth="2"
                  />
                </svg>
                <div className="absolute text-[10px] font-mono text-slate-500 left-0 top-[52px]">
                  {formatUSD(run.snapshot_balance)} snapshot
                </div>
                <div
                  className={`absolute text-[10px] font-mono right-0 top-[6px] ${pnlColor}`}
                >
                  {formatUSD(totalPnlUsd, { signed: true })}
                </div>
              </>
            ) : (
              <div className="h-full flex items-center justify-center text-[11px] font-mono text-slate-500">
                Waiting for first equity samples…
              </div>
            )}
          </div>
        </button>

        {/* 3b. CANDLE PREVIEW (Wilson chose "small preview + sub-route link") */}
        <button
          type="button"
          onClick={() => navigate(`/paper/${runId}/chart`)}
          className="bg-slate-800 border border-slate-700 rounded-xl p-4 text-left w-full active:bg-slate-700/50"
          aria-label="Recent candles — tap for full chart"
        >
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">
              Recent {run.granularity} candles
            </span>
            <span className="text-[10px] font-mono text-slate-500">
              last {previewCandles.length || PREVIEW_CANDLE_COUNT}
            </span>
          </div>
          <div className="mt-3" style={{ height: 96 }}>
            {previewCandles.length > 0 ? (
              <CandlePreview candles={previewCandles} />
            ) : (
              <div className="h-full flex items-center justify-center text-[11px] font-mono text-slate-500">
                Loading candles…
              </div>
            )}
          </div>
        </button>

        {/* 4. RECENT ACTIVITY */}
        <section
          className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden"
          aria-label="Recent activity"
        >
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-700">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">
              Recent activity
            </span>
            <span className="text-[10px] font-mono text-slate-500">
              {activity.length} of {trades.length + decisions.length}
            </span>
          </div>
          {activity.length === 0 ? (
            <div className="p-4 text-[11px] text-slate-500 italic">
              No activity yet.
            </div>
          ) : (
            <ul
              className="divide-y divide-slate-700"
              style={{ listStyle: 'none', margin: 0, padding: 0 }}
            >
              {activity.map((row, idx) => (
                <ActivityRowView
                  key={`${row.kind}-${row.ts}-${idx}`}
                  row={row}
                  instrument={run.instrument}
                />
              ))}
            </ul>
          )}
          <button
            type="button"
            onClick={() => navigate(`/paper/${runId}/trades`)}
            className="flex items-center justify-center gap-1.5 py-3 border-t border-slate-700 text-xs font-semibold text-slate-300 w-full active:bg-slate-700/50"
          >
            View all trades &amp; decisions
            <ChevronRight size={14} />
          </button>
        </section>

        {/* 5. DAILY P&L CAP */}
        {dailyLossPct != null && (
          <section
            className="bg-slate-800 border border-slate-700 rounded-xl p-4"
            aria-label="Daily P&L versus halt threshold"
          >
            <div className="flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">
                Daily P&L cap
              </span>
              <span
                className={`text-[10px] font-mono uppercase tracking-wider ${capLabelClass}`}
              >
                {capLabel}
              </span>
            </div>
            <DailyCapBar pnlPct={pnlPct ?? 0} dailyLossPct={dailyLossPct} />
            <div className="flex items-center justify-between mt-2 text-[10px] font-mono text-slate-500">
              <span className="text-rose-400">
                −{dailyLossPct.toFixed(2)}% halt
              </span>
              <span>0</span>
              <span className="text-slate-400">
                +{dailyLossPct.toFixed(2)}%
              </span>
            </div>
            <div className="mt-2 text-[11px] text-slate-400">
              <span className={`font-mono ${pnlColor}`}>
                {formatPct(pnlPct, { signed: true })}
              </span>{' '}
              today ·{' '}
              <span className="font-mono">
                {run.snapshot_balance != null && pnlPct != null
                  ? formatUSD(
                      Math.max(
                        0,
                        run.snapshot_balance * (dailyLossPct / 100) +
                          (pnlPct / 100) * run.snapshot_balance,
                      ),
                    )
                  : '—'}
              </span>{' '}
              from halt threshold
            </div>
          </section>
        )}

        {/* 6. SUB-ROUTES GRID */}
        <section
          className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden"
          aria-label="Drill-downs"
        >
          <div className="grid grid-cols-2">
            <SubRouteTile
              to={`/paper/${runId}/chart`}
              title="Chart"
              subtitle="Candles + indicators"
              borderRight
              borderBottom
            />
            <SubRouteTile
              to={`/paper/${runId}/trades`}
              title="All trades"
              subtitle={`${trades.length} · full table`}
              borderBottom
            />
            <SubRouteTile
              to={`/paper/${runId}/decisions`}
              title="Decision log"
              subtitle="Every signal fired"
              borderRight
            />
            <SubRouteTile
              to={`/paper/${runId}/risk`}
              title="Risk config"
              subtitle="SL/TP · cap · size"
            />
          </div>
        </section>

        {/* 7. META FOOTER */}
        <section
          className="px-1 py-2 flex flex-col gap-1.5"
          aria-label="Run metadata"
        >
          <MetaRow
            label="Genome"
            value={
              run.strategy_genome_id ? (
                <span className="text-slate-300 truncate">
                  {run.strategy_genome_id.slice(-16)}
                </span>
              ) : (
                <span className="text-slate-500">—</span>
              )
            }
          />
          <MetaRow
            label="Broker"
            value={
              <span className="text-slate-300 truncate">
                OANDA {run.oanda_account_id ?? '—'} ·{' '}
                {run.oanda_environment ?? 'practice'}
              </span>
            }
          />
          <MetaRow
            label="Started"
            value={
              <span className="text-slate-300">
                {run.started_at ? formatUTC(run.started_at) : '—'}
              </span>
            }
          />
          <MetaRow
            label="Last poll"
            value={
              <span className="text-emerald-400" title={lastPolledAt ?? undefined}>
                {lastPollText}
              </span>
            }
          />
        </section>
      </main>

      {/* Sticky bottom kill button */}
      <footer
        className="sticky bottom-0 z-40 px-3 pt-3"
        style={{
          paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom))',
          background:
            'linear-gradient(to top, rgba(15,23,42,1) 0%, rgba(15,23,42,1) 60%, rgba(15,23,42,0) 100%)',
        }}
      >
        <KillSwitchButton onKill={handleKill} disabledReason={killDisabledReason} />
      </footer>

    </div>
  );
}

// ─── sub-components ──────────────────────────────────────────────────

function MetaRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between text-[10px] font-mono text-slate-500 gap-3">
      <span className="shrink-0">{label}</span>
      <span className="text-right truncate min-w-0">{value}</span>
    </div>
  );
}

function SubRouteTile({
  to,
  title,
  subtitle,
  borderRight,
  borderBottom,
}: {
  to: string;
  title: string;
  subtitle: string;
  borderRight?: boolean;
  borderBottom?: boolean;
}) {
  return (
    <Link
      to={to}
      className={`px-4 py-3 flex items-center justify-between active:bg-slate-700/50 ${
        borderRight ? 'border-r border-slate-700' : ''
      } ${borderBottom ? 'border-b border-slate-700' : ''}`}
    >
      <div className="min-w-0">
        <div className="text-xs font-semibold text-slate-100">{title}</div>
        <div className="text-[10px] text-slate-500 font-mono truncate">
          {subtitle}
        </div>
      </div>
      <ChevronRight size={14} className="text-slate-500 shrink-0" />
    </Link>
  );
}

function PositionScaleBar({
  scale,
  slPips,
  tpPips,
}: {
  scale: ReturnType<typeof _computeScaleInner>;
  slPips: number;
  tpPips: number;
}) {
  const { isLong, nowPctFromLeft, entryPctFromLeft, slPrice, tpPrice, entryPrice, nowPrice, pipsInFavour } = scale;
  // Layout is always price-ascending: min-price on left, max-price on right.
  // For LONG: SL sits on left (below entry), TP on right (above entry).
  // For SHORT: TP sits on left (below entry), SL on right (above entry).
  const leftLabel = isLong ? 'SL' : 'TP';
  const rightLabel = isLong ? 'TP' : 'SL';
  const leftPrice = Math.min(slPrice, tpPrice);
  const rightPrice = Math.max(slPrice, tpPrice);
  const leftIsLoss = isLong; // LONG: left edge = SL. SHORT: left edge = TP.
  const leftColor = leftIsLoss ? 'text-rose-400' : 'text-emerald-400';
  const rightColor = leftIsLoss ? 'text-emerald-400' : 'text-rose-400';
  const leftEdgeBg = leftIsLoss ? '#fb7185' : '#34d399';
  const rightEdgeBg = leftIsLoss ? '#34d399' : '#fb7185';
  const gradient = leftIsLoss
    ? 'linear-gradient(90deg, rgba(244,63,94,0.30) 0%, rgba(100,116,139,0.25) 50%, rgba(52,211,153,0.30) 100%)'
    : 'linear-gradient(90deg, rgba(52,211,153,0.30) 0%, rgba(100,116,139,0.25) 50%, rgba(244,63,94,0.30) 100%)';

  return (
    <>
      <div className="relative" style={{ padding: '22px 0 26px' }}>
        <div
          className="w-full rounded-full"
          style={{ height: 6, background: gradient }}
        />
        <div
          className="absolute"
          style={{ left: 0, top: 0, bottom: 0, width: 2, background: leftEdgeBg }}
        />
        <div
          className="absolute"
          style={{ right: 0, top: 0, bottom: 0, width: 2, background: rightEdgeBg }}
        />
        <div
          className="absolute"
          style={{
            left: `${entryPctFromLeft}%`,
            top: 0,
            bottom: 0,
            width: 1,
            background: '#94a3b8',
          }}
        />
        <div
          className="absolute"
          style={{
            left: `${nowPctFromLeft}%`,
            top: -4,
            bottom: -4,
            width: 3,
            background: '#f1f5f9',
            borderRadius: 2,
            boxShadow: '0 0 0 3px rgba(241,245,249,0.15)',
            transform: 'translateX(-50%)',
          }}
        />
        <div className={`absolute text-[10px] font-mono ${leftColor}`} style={{ left: 0, top: 0 }}>
          {leftLabel}
        </div>
        <div className={`absolute text-[10px] font-mono ${leftColor}`} style={{ left: 0, bottom: 0 }}>
          {leftPrice.toFixed(5)}
        </div>
        <div className={`absolute text-[10px] font-mono ${rightColor}`} style={{ right: 0, top: 0 }}>
          {rightLabel}
        </div>
        <div className={`absolute text-[10px] font-mono ${rightColor}`} style={{ right: 0, bottom: 0 }}>
          {rightPrice.toFixed(5)}
        </div>
        <div
          className="absolute text-[10px] font-mono text-slate-400"
          style={{ left: `${entryPctFromLeft}%`, top: 0, transform: 'translateX(-50%)' }}
        >
          entry
        </div>
        <div
          className="absolute text-[10px] font-mono text-slate-400"
          style={{ left: `${entryPctFromLeft}%`, bottom: 0, transform: 'translateX(-50%)' }}
        >
          {entryPrice.toFixed(5)}
        </div>
        <div
          className="absolute text-[10px] font-mono text-slate-100 font-semibold"
          style={{ left: `${nowPctFromLeft}%`, top: 0, transform: 'translateX(-50%)' }}
        >
          now
        </div>
        <div
          className="absolute text-[10px] font-mono text-slate-100 font-semibold"
          style={{ left: `${nowPctFromLeft}%`, bottom: 0, transform: 'translateX(-50%)' }}
        >
          {nowPrice.toFixed(5)}
        </div>
      </div>

      <div className="flex items-center gap-3 text-[11px] font-mono text-slate-400 flex-wrap">
        <span
          className={pipsInFavour >= 0 ? 'text-emerald-400' : 'text-rose-400'}
        >
          {pipsInFavour >= 0 ? '+' : '−'}
          {Math.abs(pipsInFavour).toFixed(1)} pips {pipsInFavour >= 0 ? 'in favour' : 'against'}
        </span>
        <span className="text-slate-600">·</span>
        <span>SL {slPips}p</span>
        <span className="text-slate-600">·</span>
        <span>TP {tpPips}p</span>
      </div>
    </>
  );
}

function CandlePreview({ candles }: { candles: CandleData[] }) {
  const W = 300;
  const H = 96;
  if (candles.length === 0) return null;
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const range = max - min || 1;
  const cw = W / candles.length;
  const bodyW = Math.max(1, cw * 0.7);
  const y = (v: number) => H - ((v - min) / range) * H;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height="100%">
      {candles.map((c, i) => {
        const x = i * cw + cw / 2;
        const up = c.close >= c.open;
        const color = up ? '#34d399' : '#fb7185';
        return (
          <g key={`${c.timestamp}-${i}`}>
            <line
              x1={x}
              x2={x}
              y1={y(c.high)}
              y2={y(c.low)}
              stroke={color}
              strokeWidth={1}
            />
            <rect
              x={x - bodyW / 2}
              y={y(Math.max(c.open, c.close))}
              width={bodyW}
              height={Math.max(1, Math.abs(y(c.open) - y(c.close)))}
              fill={color}
            />
          </g>
        );
      })}
    </svg>
  );
}

function DailyCapBar({
  pnlPct,
  dailyLossPct,
}: {
  pnlPct: number;
  dailyLossPct: number;
}) {
  // Range: [-dailyLossPct, +dailyLossPct]. Marker at pnlPct.
  const halfRange = dailyLossPct;
  const clamped = Math.max(-halfRange, Math.min(halfRange, pnlPct));
  const markerPct = ((clamped + halfRange) / (halfRange * 2)) * 100;
  const markerColor = pnlPct >= 0 ? '#34d399' : '#fb7185';
  const gradient =
    'linear-gradient(90deg, rgba(244,63,94,0.45) 0%, rgba(244,63,94,0.20) 20%, rgba(100,116,139,0.25) 40%, rgba(100,116,139,0.25) 50%, rgba(52,211,153,0.20) 60%, rgba(52,211,153,0.45) 100%)';
  return (
    <div
      className="mt-3 relative border border-slate-700 rounded-md overflow-hidden"
      style={{ height: 24, background: gradient }}
      role="progressbar"
      aria-valuemin={-halfRange}
      aria-valuemax={halfRange}
      aria-valuenow={pnlPct}
    >
      <div
        className="absolute"
        style={{
          left: 0,
          top: -6,
          bottom: -6,
          width: 2,
          background: '#fb7185',
          boxShadow: '0 0 0 3px rgba(251,113,133,0.25)',
        }}
        title={`Auto-halt −${dailyLossPct.toFixed(2)}%`}
      />
      <div
        className="absolute"
        style={{
          left: '50%',
          top: -4,
          bottom: -4,
          width: 1,
          background: '#475569',
        }}
      />
      <div
        className="absolute"
        style={{
          left: `${markerPct}%`,
          top: -8,
          bottom: -8,
          width: 3,
          background: markerColor,
          borderRadius: 2,
          boxShadow: `0 0 0 3px ${markerColor}40`,
          transform: 'translateX(-50%)',
        }}
        title={`Now ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`}
      />
    </div>
  );
}

function ActivityRowView({
  row,
  instrument,
}: {
  row: ActivityRow;
  instrument: string;
}) {
  const pip = pipSizeFor(instrument);
  if (row.kind === 'trade-open') {
    const t = row.trade;
    const long = t.side === 'buy';
    return (
      <li className="px-4 py-3 flex items-start gap-3">
        <span
          className={`w-6 text-center text-sm leading-none ${long ? 'text-emerald-400' : 'text-rose-400'}`}
        >
          {long ? '▲' : '▼'}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-slate-100">
              Opened {long ? 'LONG' : 'SHORT'}
            </span>
            <span className="text-xs font-mono text-slate-400">
              {t.quantity.toLocaleString()}
            </span>
            <span className="ml-auto text-[10px] font-mono text-slate-500 shrink-0">
              {formatShortTime(t.entry_timestamp)}
            </span>
          </div>
          <div className="text-[11px] text-slate-400 font-mono mt-0.5 truncate">
            {t.signal_reason ?? '—'} @ {t.entry_price.toFixed(5)}
          </div>
        </div>
      </li>
    );
  }
  if (row.kind === 'trade-close') {
    const t = row.trade;
    const long = t.side === 'buy';
    const pnl = t.net_pnl ?? t.pnl ?? 0;
    const win = pnl >= 0;
    const pipMove =
      t.exit_price != null
        ? ((t.exit_price - t.entry_price) * (long ? 1 : -1)) / pip
        : 0;
    return (
      <li className="px-4 py-3 flex items-start gap-3">
        <span
          className={`w-6 text-center text-sm leading-none ${win ? 'text-emerald-400' : 'text-rose-400'}`}
        >
          ●
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-slate-100">
              Closed {long ? 'LONG' : 'SHORT'}
            </span>
            <span
              className={`text-xs font-mono tabular-nums ${win ? 'text-emerald-400' : 'text-rose-400'}`}
            >
              {pipMove >= 0 ? '+' : '−'}
              {Math.abs(pipMove).toFixed(1)}p ·{' '}
              {formatUSD(pnl, { signed: true })}
            </span>
            <span className="ml-auto text-[10px] font-mono text-slate-500 shrink-0">
              {formatShortTime(t.exit_timestamp ?? t.entry_timestamp)}
            </span>
          </div>
          <div className="text-[11px] text-slate-400 font-mono mt-0.5 truncate">
            exit: {t.exit_reason ?? '—'} · {t.entry_price.toFixed(5)} →{' '}
            {t.exit_price?.toFixed(5) ?? '—'}
          </div>
        </div>
      </li>
    );
  }
  const d = row.decision;
  const isWarn =
    d.action.includes('SKIPPED') ||
    d.action.includes('MISSED') ||
    d.action.includes('REJECTED');
  const glyph = d.action.includes('KILL')
    ? '⚠'
    : d.action.includes('RESUME')
      ? '↻'
      : isWarn
        ? '⊘'
        : '·';
  const glyphColor = isWarn ? 'text-slate-400' : 'text-slate-500';
  return (
    <li className="px-4 py-3 flex items-start gap-3">
      <span className={`w-6 text-center text-sm leading-none ${glyphColor}`}>
        {glyph}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-slate-300 truncate">
            {d.reason ?? d.action.replace(/^USER_/, '').replace(/_/g, ' ').toLowerCase()}
          </span>
          <span className="ml-auto text-[10px] font-mono text-slate-500 shrink-0">
            {formatShortTime(d.timestamp)}
          </span>
        </div>
        <div className="text-[11px] text-slate-500 font-mono mt-0.5 truncate">
          {d.action}
        </div>
      </div>
    </li>
  );
}
