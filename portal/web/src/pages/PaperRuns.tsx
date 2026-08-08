/**
 * Paper Trading — runs list (/paper) (US-022 / PR-I).
 *
 * Matches the wireframe: aggregate P&L header, ADMIN scope toggle
 * (My runs / All runs), 10+ concurrency banner, compact rows with
 * status pill + genome ID + OANDA account + live P&L. 5-second
 * polling matches the run-detail cadence.
 *
 * Kill on individual runs happens on the detail page (PR-J); this
 * list surfaces the run's kill_requested_at state via a "Halt
 * requested" chip so users don't lose track of in-flight halts.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Loader2 } from 'lucide-react';

import { useAuth } from '../contexts/AuthContext';
import { listPaperRuns } from '../services/paperApi';
import {
  CONCURRENCY_WARN_THRESHOLD,
  STATUS_PILL_CLASSES,
} from '../types/paper';
import type { PaperRunListItem } from '../types/paper';
import {
  canSeeScopeToggle,
  normalizeRole,
  resolveScope,
  type Scope,
} from '../utils/rbac';

const POLL_INTERVAL_MS = 5_000;

export default function PaperRuns() {
  const [runs, setRuns] = useState<PaperRunListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 25;

  const { user } = useAuth();
  const role = normalizeRole(user?.role);
  const [scope, setScope] = useState<Scope>('my');
  const showToggle = canSeeScopeToggle(role);

  const fetchRuns = useCallback(async () => {
    try {
      setError(null);
      const response = await listPaperRuns(page, pageSize, {
        scope: resolveScope(role, scope),
      });
      setRuns(response.items);
      setTotal(response.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [page, role, scope]);

  useEffect(() => {
    void fetchRuns();
    const interval = setInterval(() => {
      const hasLive = runs.some(
        (r) => r.status === 'RUNNING' || r.status === 'APPROVED',
      );
      if (hasLive) void fetchRuns();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchRuns]);

  const runningCount = runs.filter((r) => r.status === 'RUNNING').length;
  const aggregatePnl = runs.reduce((sum, r) => sum + (r.pnl ?? 0), 0);

  return (
    <div className="container mx-auto px-4 py-8">
      <div className="flex items-start justify-between mb-6 flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold">Paper Trading</h1>
          <div className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            <span className="font-semibold">{runningCount}</span> running · {total} total ·
            aggregate live P&amp;L{' '}
            <span
              className={`font-mono font-semibold ${
                aggregatePnl >= 0
                  ? 'text-emerald-700 dark:text-emerald-400'
                  : 'text-rose-700 dark:text-rose-400'
              }`}
            >
              {formatPnl(aggregatePnl)}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {showToggle && <ScopeToggle scope={scope} onChange={setScope} />}
          <TabSwitcher active="runs" />
        </div>
      </div>

      {runningCount >= CONCURRENCY_WARN_THRESHOLD && (
        <ConcurrencyBanner count={runningCount} />
      )}

      {error && (
        <div
          role="alert"
          className="mb-4 px-4 py-3 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-300"
        >
          {error}
        </div>
      )}

      {loading && runs.length === 0 ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-8 h-8 text-emerald-500 animate-spin" />
        </div>
      ) : runs.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="space-y-3">
          {runs.map((run) => (
            <RunRow key={run.id} run={run} />
          ))}
        </div>
      )}
    </div>
  );
}

function RunRow({ run }: { run: PaperRunListItem }) {
  const pnl = run.pnl ?? 0;
  const pnlPct =
    run.pnl != null && run.snapshot_balance && run.snapshot_balance > 0
      ? (pnl / run.snapshot_balance) * 100
      : null;

  return (
    <Link
      to={`/paper/${run.id}`}
      className="block bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5 hover:shadow-md transition"
    >
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-4 min-w-0">
          <StatusPill status={run.status} />
          <div className="min-w-0">
            {/* Name row (user-set at deploy or LLM-suggested).
                Falls back to the instrument+granularity line when
                unnamed so tiles never render nameless. */}
            {run.name ? (
              <>
                <div className="font-semibold truncate">{run.name}</div>
                <div className="text-xs text-slate-500 dark:text-slate-400 truncate">
                  {run.instrument} {run.granularity}
                </div>
              </>
            ) : (
              <div className="font-semibold truncate">
                {run.instrument} {run.granularity}
              </div>
            )}
            <div className="text-xs text-slate-500 dark:text-slate-400 font-mono flex items-center gap-1.5 flex-wrap mt-0.5">
              <span className="truncate">{run.id}</span>
              {run.oanda_account_id && (
                <span className="bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 px-1.5 py-0.5 rounded normal-case">
                  acct …{run.oanda_account_id.slice(-4)}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-6 text-sm">
          <div className="text-right">
            <div className="text-xs text-slate-500">Live P&amp;L</div>
            <div
              className={`font-mono font-semibold ${
                pnl >= 0
                  ? 'text-emerald-700 dark:text-emerald-400'
                  : 'text-rose-700 dark:text-rose-400'
              }`}
            >
              {formatPnl(pnl)}
              {pnlPct != null && (
                <span className="ml-1 text-xs">
                  ({pnlPct >= 0 ? '+' : ''}
                  {pnlPct.toFixed(2)}%)
                </span>
              )}
            </div>
          </div>
          {run.stop_reason && (
            <div className="text-right">
              <div className="text-xs text-slate-500">Stop reason</div>
              <div className="text-xs font-mono">{run.stop_reason}</div>
            </div>
          )}
          <div className="text-slate-400">›</div>
        </div>
      </div>
    </Link>
  );
}

function StatusPill({ status }: { status: PaperRunListItem['status'] }) {
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

function ScopeToggle({
  scope,
  onChange,
}: {
  scope: Scope;
  onChange: (next: Scope) => void;
}) {
  const base = 'text-xs font-semibold rounded px-3 py-1.5 transition-colors';
  return (
    <div
      role="radiogroup"
      aria-label="Paper run scope"
      className="flex gap-0.5 p-0.5 rounded border border-indigo-300 dark:border-indigo-700 bg-white dark:bg-slate-800"
    >
      <button
        type="button"
        role="radio"
        aria-checked={scope === 'my'}
        onClick={() => onChange('my')}
        className={`${base} ${
          scope === 'my'
            ? 'bg-indigo-600 text-white'
            : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-200'
        }`}
      >
        My runs
      </button>
      <button
        type="button"
        role="radio"
        aria-checked={scope === 'all'}
        onClick={() => onChange('all')}
        className={`${base} ${
          scope === 'all'
            ? 'bg-indigo-600 text-white'
            : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-200'
        }`}
      >
        All runs
      </button>
    </div>
  );
}

function TabSwitcher({ active }: { active: 'runs' | 'approvals' }) {
  return (
    <div className="flex gap-1 text-xs bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded p-0.5">
      <Link
        to="/paper"
        className={`rounded px-3 py-1.5 font-semibold ${
          active === 'runs'
            ? 'bg-emerald-500 text-white'
            : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-200'
        }`}
      >
        Runs
      </Link>
      <Link
        to="/paper/approvals"
        className={`rounded px-3 py-1.5 font-semibold ${
          active === 'approvals'
            ? 'bg-emerald-500 text-white'
            : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-200'
        }`}
      >
        Approvals
      </Link>
    </div>
  );
}

function ConcurrencyBanner({ count }: { count: number }) {
  return (
    <div className="mb-4 bg-amber-50 dark:bg-amber-900/20 border border-amber-300 dark:border-amber-700 rounded-lg p-4 flex items-start gap-3">
      <div className="w-8 h-8 bg-amber-200 dark:bg-amber-800 rounded-full flex items-center justify-center text-amber-800 dark:text-amber-200 text-sm shrink-0">
        <AlertTriangle className="w-4 h-4" />
      </div>
      <div className="flex-1 text-sm">
        <div className="font-semibold text-amber-900 dark:text-amber-200">
          You have {count} running paper deployments
        </div>
        <div className="text-xs text-amber-800 dark:text-amber-300 mt-0.5">
          Broker connection quotas start applying above{' '}
          {CONCURRENCY_WARN_THRESHOLD} concurrent runs (OANDA v20 REST: 120
          req/s per token, shared across all your runs). Latency and hit rate
          on the newest runs may degrade. Consider halting any that have
          finished their evaluation window.
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-12">
      <div className="max-w-md mx-auto text-center">
        <div className="w-16 h-16 bg-slate-100 dark:bg-slate-700 rounded-full mx-auto flex items-center justify-center text-3xl text-slate-400">
          📊
        </div>
        <div className="text-lg font-semibold mt-4">No paper runs yet</div>
        <div className="text-sm text-slate-500 dark:text-slate-400 mt-1">
          Approve a validated genome to deploy it here.
        </div>
        <Link
          to="/paper/approvals"
          className="mt-6 inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-sm px-4 py-2 rounded"
        >
          Go to approval queue →
        </Link>
      </div>
    </div>
  );
}

function formatPnl(v: number): string {
  const abs = Math.abs(v);
  return `${v >= 0 ? '+' : '-'}$${abs.toFixed(2)}`;
}
