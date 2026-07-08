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
import { AlertCircle, ChevronLeft, Loader2 } from 'lucide-react';

import { useToast } from '../contexts/ToastContext';
import { KillSwitchButton } from '../components/KillSwitchButton';
import { Play } from 'lucide-react';
import {
  LivePnLChart,
  type EquityPoint,
} from '../components/LivePnLChart';
import {
  getPaperRun,
  killPaperRun,
  resumePaperRun,
} from '../services/paperApi';
import {
  isTerminal,
  STATUS_PILL_CLASSES,
  type PaperRunPublic,
  type PaperRunStatus,
} from '../types/paper';

const POLL_INTERVAL_MS = 5_000;
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
      />

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
              Dual x-axis: relative hours (bottom) + actual UTC (top). Live
              samples accumulate every 5s while this tab is open.
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
          />
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
        <AccountRiskPanel run={run} />
        <RunLifecyclePanel run={run} />
        <RiskConfigPanel run={run} />
      </div>

      <div className="mt-6 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
        <div className="text-sm font-semibold mb-2">Trades</div>
        <div className="text-xs text-slate-500 dark:text-slate-400 italic">
          Trades table (with Origin + Signal reason columns) wires up in
          v0.6.1 alongside the /paper-runs/{`{id}`}/trades endpoint. For now
          the run's live P&amp;L above is the source of truth.
        </div>
      </div>

      <div className="mt-6 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
        <div className="text-sm font-semibold mb-2">Decision log</div>
        <div className="text-xs text-slate-500 dark:text-slate-400 italic">
          Decision log stream lands in v0.6.1. Every gate refusal, order
          placement, and kill request is already persisted server-side via
          the `decision_logs` table; surfacing it here needs a small
          /paper-runs/{`{id}`}/decisions endpoint.
        </div>
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
}: {
  run: PaperRunPublic;
  pnl: number;
  pnlPct: number;
  onKill: () => Promise<void>;
  onResume: () => Promise<void>;
}) {
  const disabledReason = useMemo(() => {
    if (run.kill_requested_at) return 'Kill already requested';
    if (isTerminal(run.status)) return `Run is ${run.status.toLowerCase()}`;
    return null;
  }, [run.kill_requested_at, run.status]);

  // Only HALTED_MANUAL is resumable. ERROR runs stay terminal because
  // their failure encodes a real problem the runner can't fix by
  // itself (broker circuit, credentials revoked). COMPLETED runs
  // wouldn't be reached here — the user would redeploy.
  const canResume = run.status === 'HALTED_MANUAL';

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
        {canResume ? (
          <ResumeButton onResume={onResume} />
        ) : (
          <KillSwitchButton onKill={onKill} disabledReason={disabledReason} />
        )}
      </div>
    </div>
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
            {run.approved_at
              ? new Date(run.approved_at).toLocaleString()
              : '—'}
          </span>
        </Row>
        <Row label="Started">
          <span className="font-mono text-xs">
            {run.started_at
              ? new Date(run.started_at).toLocaleString()
              : '—'}
          </span>
        </Row>
        <Row label="Kill requested">
          <span className="font-mono text-xs">
            {run.kill_requested_at
              ? new Date(run.kill_requested_at).toLocaleString()
              : '—'}
          </span>
        </Row>
        <Row label="Completed">
          <span className="font-mono text-xs">
            {run.completed_at
              ? new Date(run.completed_at).toLocaleString()
              : '—'}
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
          <span className="font-mono">
            {cfg?.max_position_size_units.toLocaleString() ?? '—'} units
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
