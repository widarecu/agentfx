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
                className="px-3 py-2 text-left font-medium"
                title="Volatility quintile × trend-strength label computed on the slice's candle data"
              >
                Regime
              </th>
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
                  <td className="px-3 py-2 text-xs">
                    {f.regime_volatility && f.regime_trend ? (
                      <span
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200"
                        title={
                          `Volatility: ${f.regime_volatility}; ` +
                          `Trend: ${f.regime_trend}` +
                          (f.regime_direction
                            ? `; Direction: ${f.regime_direction}`
                            : '')
                        }
                      >
                        {f.regime_volatility}-vol / {f.regime_trend}
                        {f.regime_direction && (
                          <span
                            className={
                              f.regime_direction === 'up'
                                ? 'text-emerald-600 dark:text-emerald-400 font-medium'
                                : f.regime_direction === 'down'
                                  ? 'text-rose-600 dark:text-rose-400 font-medium'
                                  : 'text-slate-500 dark:text-slate-400'
                            }
                          >
                            {' / '}
                            {f.regime_direction === 'up'
                              ? '↑ up'
                              : f.regime_direction === 'down'
                                ? '↓ down'
                                : '→ flat'}
                          </span>
                        )}
                      </span>
                    ) : (
                      <span className="text-slate-400 dark:text-slate-500">—</span>
                    )}
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
        the delta from it. With multiple OOS folds, the validator's
        promotion verdict uses the <strong>median</strong> across them
        for Sharpe / MaxDD / hit-rate / CAGR (sum for trade count) — so
        one bad slice no longer poisons the whole result.
      </p>
      <OosMedianSummary folds={folds} />
      <ConditionalEdgeBadge folds={folds} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// OOS median summary row (v0.4.0)
// ─────────────────────────────────────────────────────────────────────

function OosMedianSummary({ folds }: { folds: ValidationFold[] }) {
  const oos = folds.filter((f) => f.is_oos);
  if (oos.length < 2) return null;

  const median = (vals: number[]): number | null => {
    const finite = vals.filter((v) => Number.isFinite(v));
    if (finite.length === 0) return null;
    const sorted = [...finite].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2
      ? sorted[mid]
      : (sorted[mid - 1] + sorted[mid]) / 2;
  };

  const medianSharpe = median(
    oos.map((f) => f.sharpe).filter((s): s is number => s != null),
  );
  const medianHit = median(
    oos.map((f) => f.hit_rate).filter((h): h is number => h != null),
  );
  const medianCagr = median(
    oos.map((f) => f.cagr).filter((c): c is number => c != null),
  );
  const totalTrades = oos.reduce((sum, f) => sum + (f.n_trades ?? 0), 0);

  const fmt = (v: number | null, digits = 2) =>
    v == null ? '—' : v.toFixed(digits);

  const sharpePasses = medianSharpe != null ? medianSharpe >= 1.0 : null;

  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 px-4 py-3 bg-slate-50 dark:bg-slate-800/40">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-400 mb-2">
        Median across {oos.length} OOS folds (v0.4.0 verdict)
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <div>
          <div className="text-xs text-slate-500 dark:text-slate-400">
            Median Sharpe
          </div>
          <div
            className={`font-mono ${
              sharpePasses == null
                ? ''
                : sharpePasses
                  ? 'text-green-600 dark:text-green-400'
                  : 'text-red-600 dark:text-red-400'
            }`}
          >
            {fmt(medianSharpe)}
          </div>
        </div>
        <div>
          <div className="text-xs text-slate-500 dark:text-slate-400">
            Median Hit Rate
          </div>
          <div className="font-mono">
            {medianHit != null ? `${(medianHit * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div>
          <div className="text-xs text-slate-500 dark:text-slate-400">
            Median CAGR
          </div>
          <div className="font-mono">
            {medianCagr != null ? `${(medianCagr * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div>
          <div className="text-xs text-slate-500 dark:text-slate-400">
            Total OOS Trades
          </div>
          <div className="font-mono">{totalTrades}</div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Conditional-edge classifier (v0.4.0 follow-up, step 3)
// ─────────────────────────────────────────────────────────────────────
//
// Tags the genome with one of five categories based on per-OOS-fold
// Sharpe ↔ direction-regime correlation. Purely descriptive — does NOT
// feed into Gate B, validation, or any selection logic. The point is to
// preserve optionality on directionally-biased strategies: a trader may
// want to deploy a strategy that's brilliant in trending markets but
// useless in chop, gated on a macro/calendar regime forecast. Hiding
// these strategies from the human reviewer because the rolling median
// is mediocre would throw away that optionality.
//
// Categories:
//
//   Robust       — median OOS Sharpe ≥ 1.0 AND no fold below -0.5
//                  (works across regimes, primary paper-trade candidate)
//   Conditional  — strong positive in one direction (up OR down) and
//                  weak/negative in another (deploy on regime forecast)
//   Mixed        — overall positive but no clear direction-Sharpe link
//                  (noisy edge, worth monitoring but not safe to deploy)
//   Spurious     — overall negative with no clear conditional pattern
//                  (just doesn't work)
//   Insufficient — fewer than 3 OOS folds or no direction tags
//                  (rolling design not yet active for this genome)

type EdgeCategory =
  | 'robust'
  | 'conditional-up'
  | 'conditional-down'
  | 'mixed'
  | 'spurious'
  | 'insufficient';

interface EdgeClassification {
  category: EdgeCategory;
  detail: string;
  medianSharpe: number | null;
}

function classifyConditionalEdge(folds: ValidationFold[]): EdgeClassification {
  const oos = folds.filter((f) => f.is_oos);
  if (oos.length < 3) {
    return {
      category: 'insufficient',
      detail: 'Fewer than 3 OOS folds — rolling design not active for this genome.',
      medianSharpe: null,
    };
  }

  const median = (vals: number[]): number | null => {
    if (vals.length === 0) return null;
    const sorted = [...vals].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2
      ? sorted[mid]
      : (sorted[mid - 1] + sorted[mid]) / 2;
  };

  const allSharpes = oos
    .map((f) => f.sharpe)
    .filter((s): s is number => s != null && Number.isFinite(s));
  const overallMed = median(allSharpes);
  const minSharpe = allSharpes.length > 0 ? Math.min(...allSharpes) : null;

  // Robust: passes Gate B's Sharpe threshold on the median AND no
  // catastrophic fold (something worse than -0.5 Sharpe means a real
  // 22-day window would have lost meaningful money).
  if (overallMed != null && overallMed >= 1.0 && minSharpe != null && minSharpe > -0.5) {
    return {
      category: 'robust',
      detail:
        `Median OOS Sharpe ${overallMed.toFixed(2)} with no fold worse ` +
        `than ${minSharpe.toFixed(2)}. Works across regimes.`,
      medianSharpe: overallMed,
    };
  }

  // Conditional: split by direction tag, see if one direction is
  // consistently profitable while the other isn't.
  const taggedFolds = oos.filter((f) => f.regime_direction && f.sharpe != null);
  if (taggedFolds.length >= 3) {
    const byDir: Record<string, number[]> = { up: [], down: [], flat: [] };
    for (const f of taggedFolds) {
      const dir = f.regime_direction;
      if (dir && f.sharpe != null) byDir[dir]?.push(f.sharpe);
    }
    const medUp = median(byDir.up);
    const medDown = median(byDir.down);
    const FAVORED = 1.0; // good direction must clear Gate B Sharpe
    const OPPOSED = 0; // bad direction must be at or below break-even

    if (medUp != null && medDown != null) {
      if (
        medUp >= FAVORED &&
        medDown <= OPPOSED &&
        byDir.up.length >= 1 &&
        byDir.down.length >= 1
      ) {
        return {
          category: 'conditional-up',
          detail:
            `Median Sharpe in up-trending OOS folds: ${medUp.toFixed(2)} ` +
            `(${byDir.up.length}); in down-trending folds: ` +
            `${medDown.toFixed(2)} (${byDir.down.length}). Edge depends on ` +
            `directional regime — could be deployed when an up-trending ` +
            `regime is forecast.`,
          medianSharpe: overallMed,
        };
      }
      if (
        medDown >= FAVORED &&
        medUp <= OPPOSED &&
        byDir.up.length >= 1 &&
        byDir.down.length >= 1
      ) {
        return {
          category: 'conditional-down',
          detail:
            `Median Sharpe in down-trending OOS folds: ${medDown.toFixed(2)} ` +
            `(${byDir.down.length}); in up-trending folds: ` +
            `${medUp.toFixed(2)} (${byDir.up.length}). Edge depends on ` +
            `directional regime — could be deployed when a down-trending ` +
            `regime is forecast.`,
          medianSharpe: overallMed,
        };
      }
    }
  }

  // No clear conditional pattern. Bucket by sign of overall median.
  if (overallMed != null && overallMed >= 0) {
    return {
      category: 'mixed',
      detail:
        `Median OOS Sharpe ${overallMed.toFixed(2)} but no clear correlation ` +
        `between fold direction and Sharpe. Noisy edge — not deployable ` +
        `without further conditioning.`,
      medianSharpe: overallMed,
    };
  }

  return {
    category: 'spurious',
    detail: `Median OOS Sharpe ${overallMed?.toFixed(2) ?? '—'}. Does not work across regimes.`,
    medianSharpe: overallMed,
  };
}

function ConditionalEdgeBadge({ folds }: { folds: ValidationFold[] }) {
  const cls = classifyConditionalEdge(folds);
  if (cls.category === 'insufficient') return null;

  const styles: Record<EdgeCategory, { bg: string; label: string; icon: string }> = {
    robust: {
      bg: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-800 dark:text-emerald-200 border-emerald-200 dark:border-emerald-800',
      label: 'Robust',
      icon: '✓',
    },
    'conditional-up': {
      bg: 'bg-sky-50 dark:bg-sky-900/20 text-sky-800 dark:text-sky-200 border-sky-200 dark:border-sky-800',
      label: 'Conditional · works in UP regimes',
      icon: '↑',
    },
    'conditional-down': {
      bg: 'bg-violet-50 dark:bg-violet-900/20 text-violet-800 dark:text-violet-200 border-violet-200 dark:border-violet-800',
      label: 'Conditional · works in DOWN regimes',
      icon: '↓',
    },
    mixed: {
      bg: 'bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200 border-amber-200 dark:border-amber-800',
      label: 'Mixed',
      icon: '~',
    },
    spurious: {
      bg: 'bg-rose-50 dark:bg-rose-900/20 text-rose-800 dark:text-rose-200 border-rose-200 dark:border-rose-800',
      label: 'Spurious',
      icon: '✗',
    },
    insufficient: { bg: '', label: '', icon: '' },
  };
  const style = styles[cls.category];

  return (
    <div className={`rounded-lg border px-4 py-3 text-sm flex items-start gap-3 ${style.bg}`}>
      <span className="font-bold text-base leading-none mt-0.5">{style.icon}</span>
      <div className="flex-1">
        <div className="font-medium">{style.label}</div>
        <div className="text-xs mt-0.5 opacity-80">{cls.detail}</div>
        <div className="text-[10px] mt-1 opacity-70 italic">
          Descriptive only — does not affect Gate B or selection. Surfaces
          directionally-biased edge for conditional deployment.
        </div>
      </div>
    </div>
  );
}
