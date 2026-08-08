/**
 * Compact card for one row in the approval queue (US-022 / PR-I).
 *
 * Gate B thresholds are evaluated per-metric so the UI can show each
 * one green / amber / rose independently (per Wilson's design
 * decision on the wireframe: "metrics are coloured by *individual*
 * Gate B threshold status, not by overall pass/fail"). Cards that
 * fully clear Gate B get a subtle emerald border.
 *
 * Approve wires to a parent-provided callback — PR-J owns the deploy
 * modal with the OANDA account picker. For PR-I the Approve button
 * shows a "coming in next update" toast so users see the button
 * exists but understand the flow isn't complete.
 */

import React from 'react';
import { CheckCircle2, XCircle, AlertCircle } from 'lucide-react';

import type { ApprovalQueueItem, MetricVerdict } from '../types/paper';
import {
  clearsGateB,
  evaluateHitRate,
  evaluateMaxDrawdown,
  evaluateSharpe,
  evaluateTrades,
} from '../types/paper';

interface ApprovalQueueCardProps {
  item: ApprovalQueueItem;
  /** Called when the user clicks Approve. The parent shows the
   *  deploy modal (PR-J) or a placeholder toast in PR-I. */
  onApprove: (item: ApprovalQueueItem) => void;
  onReject: (item: ApprovalQueueItem) => void;
}

export function ApprovalQueueCard({
  item,
  onApprove,
  onReject,
}: ApprovalQueueCardProps) {
  const m = item.metrics_summary ?? {};
  const sharpe = toNum(m.sharpe);
  const maxDd = toNum(m.max_drawdown);
  const hitRate = toNum(m.hit_rate);
  const trades = toNum(m.trades) ?? toNum(m.n_trades);
  const cagr = toNum(m.cagr);

  const passesAll = clearsGateB(m);
  const cardBorder = passesAll
    ? 'border-2 border-emerald-400 dark:border-emerald-600'
    : 'border border-slate-200 dark:border-slate-700';

  return (
    <div
      className={`bg-white dark:bg-slate-800 rounded-lg p-5 ${cardBorder}`}
      data-testid="approval-queue-card"
    >
      <div className="flex items-start justify-between gap-6">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-3 mb-1 flex-wrap">
            <div className="text-sm font-semibold">
              {item.instrument} {item.granularity}
            </div>
            <GateBSummaryBadge passesAll={passesAll} />
            {item.imported_from_email && (
              <span
                className="px-1.5 py-0.5 text-[10px] uppercase tracking-wider font-semibold rounded bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                title={`Imported from ${item.imported_from_email}. Trust posture: content-hash verified (tamper detection), sender NOT authenticated.`}
              >
                Imported
              </span>
            )}
          </div>
          <div className="text-xs text-slate-500 dark:text-slate-400 font-mono mb-3 truncate">
            {item.strategy_genome_id}
          </div>
          <div className="grid grid-cols-5 gap-4">
            <Metric label="OOS Sharpe" value={fmt(sharpe, 2)} verdict={evaluateSharpe(sharpe)} />
            <Metric label="Hit rate" value={fmtPct(hitRate)} verdict={evaluateHitRate(hitRate)} />
            <Metric
              label="Trades"
              value={trades == null ? '—' : String(trades)}
              verdict={evaluateTrades(trades)}
            />
            <Metric
              label="CAGR"
              value={cagr == null ? '—' : `${(cagr * 100).toFixed(1)}%`}
              // CAGR isn't a Gate B threshold on its own but users think
              // of it as an at-a-glance signal. Colour neutrally.
              verdict={cagr != null && cagr > 0 ? 'pass' : 'borderline'}
            />
            <Metric
              label="Max DD"
              value={maxDd == null ? '—' : fmtPct(Math.abs(maxDd))}
              verdict={evaluateMaxDrawdown(maxDd)}
            />
          </div>
        </div>
        <div className="flex flex-col gap-2 shrink-0">
          <button
            type="button"
            onClick={() => onApprove(item)}
            className="bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold px-4 py-2 rounded shadow"
          >
            Approve →
          </button>
          <button
            type="button"
            onClick={() => onReject(item)}
            className="text-sm text-slate-500 dark:text-slate-400 px-4 py-2 hover:text-rose-700 dark:hover:text-rose-400"
          >
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}

function GateBSummaryBadge({ passesAll }: { passesAll: boolean }) {
  if (passesAll) {
    return (
      <span className="inline-flex items-center gap-1 bg-emerald-100 dark:bg-emerald-900/40 text-emerald-800 dark:text-emerald-300 text-xs font-semibold px-2 py-0.5 rounded">
        <CheckCircle2 className="w-3 h-3" /> Clears Gate B
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-300 text-xs font-semibold px-2 py-0.5 rounded">
      <AlertCircle className="w-3 h-3" /> Below threshold
    </span>
  );
}

function Metric({
  label,
  value,
  verdict,
}: {
  label: string;
  value: string;
  verdict: MetricVerdict;
}) {
  const colour =
    verdict === 'pass'
      ? 'text-emerald-700 dark:text-emerald-400'
      : verdict === 'borderline'
      ? 'text-amber-700 dark:text-amber-400'
      : 'text-rose-700 dark:text-rose-400';
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div
        className={`mt-1 text-sm font-mono font-semibold ${colour}`}
        aria-label={`${label}: ${value}, ${verdict}`}
      >
        {value}
        {verdict === 'fail' && (
          <XCircle className="inline w-3 h-3 ml-1" aria-hidden="true" />
        )}
      </div>
    </div>
  );
}

function toNum(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string') {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function fmt(v: number | null, digits = 2): string {
  return v == null ? '—' : v.toFixed(digits);
}

function fmtPct(v: number | null): string {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}
