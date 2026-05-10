/**
 * WalkForwardFoldTable — renders the per-fold metrics for a genome.
 *
 * The OOS fold (is_oos=True) is highlighted because that's the fold the
 * validator's Gate B applies to. Training-fold metrics are recorded for
 * the audit trail but don't gate promotion.
 */

import React from 'react';
import type { ValidationFold } from '../types/evolution';

interface WalkForwardFoldTableProps {
  folds: ValidationFold[];
}

function formatNum(v: number | null | undefined, digits = 3): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

function formatPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

export default function WalkForwardFoldTable({
  folds,
}: WalkForwardFoldTableProps) {
  if (folds.length === 0) {
    return (
      <div className="text-sm text-slate-500 dark:text-slate-400 italic">
        Not yet validated.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 dark:bg-slate-800">
          <tr className="text-xs uppercase tracking-wide text-slate-600 dark:text-slate-400">
            <th className="px-3 py-2 text-left font-medium">Fold</th>
            <th className="px-3 py-2 text-left font-medium">Range</th>
            <th className="px-3 py-2 text-right font-medium">Sharpe</th>
            <th className="px-3 py-2 text-right font-medium">Max DD</th>
            <th className="px-3 py-2 text-right font-medium">Hit rate</th>
            <th className="px-3 py-2 text-right font-medium">Trades</th>
            <th className="px-3 py-2 text-center font-medium">Gate B</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
          {folds.map((f) => {
            const startStr = f.fold_start.slice(0, 10);
            const endStr = f.fold_end.slice(0, 10);
            const oosClass = f.is_oos
              ? 'bg-amber-50 dark:bg-amber-900/20 font-medium'
              : 'bg-white dark:bg-slate-900';
            return (
              <tr key={f.id} className={oosClass}>
                <td className="px-3 py-2 whitespace-nowrap">
                  #{f.fold_index}{' '}
                  {f.is_oos && (
                    <span className="ml-1 px-1.5 py-0.5 text-[10px] rounded bg-amber-200 dark:bg-amber-800 text-amber-800 dark:text-amber-100 uppercase tracking-wide">
                      OOS
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-slate-600 dark:text-slate-400 font-mono text-xs">
                  {startStr} → {endStr}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {formatNum(f.sharpe, 2)}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {formatPct(f.max_drawdown)}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {formatPct(f.hit_rate)}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {f.n_trades ?? '—'}
                </td>
                <td className="px-3 py-2 text-center">
                  {f.passed_gate_b ? (
                    <span className="inline-block px-2 py-0.5 text-xs rounded bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300">
                      pass
                    </span>
                  ) : (
                    <span className="inline-block px-2 py-0.5 text-xs rounded bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300">
                      fail
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
