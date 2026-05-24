/**
 * WalkForwardFoldTable — renders the per-fold metrics for a genome.
 *
 * The OOS fold (is_oos=True) is highlighted because that's the fold the
 * validator's Gate B applies to. Training-fold metrics are recorded for
 * the audit trail but don't gate promotion.
 *
 * Each metric cell is independently colored against the Gate B threshold
 * (green = passes that threshold, red = fails) so a reader can answer at
 * a glance: "why did this fold fail?" — was it Sharpe, max DD, hit rate,
 * or trade count?
 *
 * Gate B thresholds are mirrored from `src/agentfx/core/gates.py`
 * (MVP_CONSTRAINTS). Changing them is a roadmap-level decision; the
 * source of truth is the backend.
 */

import React from 'react';
import type { ValidationFold } from '../types/evolution';

const GATE_B = {
  sharpeMin: 1.0,
  maxDrawdown: 0.1, // 10%
  hitRateMin: 0.48, // 48%
  minTrades: 300,
  minCagr: 0.15, // 15% net per year
};

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

function formatPctMagnitude(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(Math.abs(v) * 100).toFixed(1)}%`;
}

const PASS_CLS =
  'text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/15';
const FAIL_CLS =
  'text-rose-700 dark:text-rose-400 bg-rose-50 dark:bg-rose-900/15';
const NEUTRAL_CLS = 'text-slate-700 dark:text-slate-300';

function metricCellClass(passes: boolean | null): string {
  if (passes == null) return NEUTRAL_CLS;
  return passes ? PASS_CLS : FAIL_CLS;
}

function gateTooltip(
  label: string,
  threshold: string,
  passes: boolean,
  delta: string,
): string {
  return `${label}: threshold ${threshold} — ${passes ? 'PASS' : 'FAIL'} (${delta})`;
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
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 dark:bg-slate-800">
            <tr className="text-xs uppercase tracking-wide text-slate-600 dark:text-slate-400">
              <th className="px-3 py-2 text-left font-medium">Fold</th>
              <th className="px-3 py-2 text-left font-medium">Range</th>
              <th
                className="px-3 py-2 text-right font-medium"
                title="Gate B threshold ≥ 1.0"
              >
                Sharpe (≥ {GATE_B.sharpeMin.toFixed(1)})
              </th>
              <th
                className="px-3 py-2 text-right font-medium"
                title={`Gate B cap ${(GATE_B.maxDrawdown * 100).toFixed(0)}% (magnitude)`}
              >
                Max DD (cap {(GATE_B.maxDrawdown * 100).toFixed(0)}%)
              </th>
              <th
                className="px-3 py-2 text-right font-medium"
                title={`Gate B threshold ≥ ${(GATE_B.hitRateMin * 100).toFixed(0)}%`}
              >
                Hit rate (≥ {(GATE_B.hitRateMin * 100).toFixed(0)}%)
              </th>
              <th
                className="px-3 py-2 text-right font-medium"
                title={`Gate B threshold ≥ ${GATE_B.minTrades}`}
              >
                Trades (≥ {GATE_B.minTrades})
              </th>
              <th
                className="px-3 py-2 text-right font-medium"
                title={`Gate B threshold ≥ ${(GATE_B.minCagr * 100).toFixed(0)}%`}
              >
                CAGR (≥ {(GATE_B.minCagr * 100).toFixed(0)}%/yr)
              </th>
              <th className="px-3 py-2 text-center font-medium">Gate B</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
            {folds.map((f) => {
              const startStr = f.fold_start.slice(0, 10);
              const endStr = f.fold_end.slice(0, 10);
              const oosClass = f.is_oos
                ? 'bg-amber-50/30 dark:bg-amber-900/10 font-medium'
                : 'bg-white dark:bg-slate-900';

              const sharpePasses =
                f.sharpe != null ? f.sharpe >= GATE_B.sharpeMin : null;
              const ddPasses =
                f.max_drawdown != null
                  ? f.max_drawdown <= GATE_B.maxDrawdown
                  : null;
              const hitPasses =
                f.hit_rate != null ? f.hit_rate >= GATE_B.hitRateMin : null;
              const tradesPasses =
                f.n_trades != null ? f.n_trades >= GATE_B.minTrades : null;
              const cagrPasses =
                f.cagr != null ? f.cagr >= GATE_B.minCagr : null;

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
                  <td
                    className={`px-3 py-2 text-right font-mono ${metricCellClass(sharpePasses)}`}
                    title={
                      sharpePasses != null && f.sharpe != null
                        ? gateTooltip(
                            'Sharpe',
                            `≥ ${GATE_B.sharpeMin.toFixed(1)}`,
                            sharpePasses,
                            `${sharpePasses ? '+' : ''}${(f.sharpe - GATE_B.sharpeMin).toFixed(2)} vs threshold`,
                          )
                        : 'Sharpe — no data'
                    }
                  >
                    {formatNum(f.sharpe, 2)}
                  </td>
                  <td
                    className={`px-3 py-2 text-right font-mono ${metricCellClass(ddPasses)}`}
                    title={
                      ddPasses != null && f.max_drawdown != null
                        ? gateTooltip(
                            'Max DD',
                            `cap ${(GATE_B.maxDrawdown * 100).toFixed(0)}%`,
                            ddPasses,
                            `${(
                              (Math.abs(f.max_drawdown) - GATE_B.maxDrawdown) *
                              100
                            ).toFixed(1)}pp vs cap`,
                          )
                        : 'Max DD — no data'
                    }
                  >
                    {formatPctMagnitude(f.max_drawdown)}
                  </td>
                  <td
                    className={`px-3 py-2 text-right font-mono ${metricCellClass(hitPasses)}`}
                    title={
                      hitPasses != null && f.hit_rate != null
                        ? gateTooltip(
                            'Hit rate',
                            `≥ ${(GATE_B.hitRateMin * 100).toFixed(0)}%`,
                            hitPasses,
                            `${((f.hit_rate - GATE_B.hitRateMin) * 100).toFixed(1)}pp vs threshold`,
                          )
                        : 'Hit rate — no data'
                    }
                  >
                    {formatPct(f.hit_rate)}
                  </td>
                  <td
                    className={`px-3 py-2 text-right font-mono ${metricCellClass(tradesPasses)}`}
                    title={
                      tradesPasses != null && f.n_trades != null
                        ? gateTooltip(
                            'Trades',
                            `≥ ${GATE_B.minTrades}`,
                            tradesPasses,
                            `${f.n_trades >= GATE_B.minTrades ? '+' : ''}${f.n_trades - GATE_B.minTrades} vs threshold`,
                          )
                        : 'Trades — no data'
                    }
                  >
                    {f.n_trades ?? '—'}
                  </td>
                  <td
                    className={`px-3 py-2 text-right font-mono ${metricCellClass(cagrPasses)}`}
                    title={
                      cagrPasses != null && f.cagr != null
                        ? gateTooltip(
                            'CAGR',
                            `≥ ${(GATE_B.minCagr * 100).toFixed(0)}%`,
                            cagrPasses,
                            `${((f.cagr - GATE_B.minCagr) * 100).toFixed(1)}pp vs threshold`,
                          )
                        : 'CAGR — no data'
                    }
                  >
                    {formatPct(f.cagr)}
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
      <p className="text-xs text-slate-500 dark:text-slate-400 italic">
        Each metric cell is colored by whether it passes its Gate B
        threshold independently. Hover any cell to see the threshold and
        the delta from it.
      </p>
    </div>
  );
}
