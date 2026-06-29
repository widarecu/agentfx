/**
 * FitnessBreakdown — Genome fitness scoring panel (v0.6.0 Gate-B-proximity).
 *
 * Shows the five weighted Gate-B-proximity terms that make up the raw fitness
 * composite, followed by the simplified penalty chain (zero-trade hard floor
 * and diversity penalty). The three stale rows from pre-v0.6.0 (trade-
 * shortfall, drawdown-breach, Gate-B-dilution) are not rendered — they are
 * no-ops in the new formula and showing them as "(not applied)" would be
 * misleading.
 *
 * Extracted from GenomeDetail.tsx so it can be unit-tested independently.
 */

import React from 'react';
import {
  buildFitnessBreakdownData,
  fmtSigned,
  type ProximityTerm,
} from '../utils/fitnessBreakdown';
import type { GenomeMetrics } from '../types/evolution';

// ── Types ──────────────────────────────────────────────────────────────

interface FitnessBreakdownProps {
  breakdown: NonNullable<GenomeMetrics['penalty_breakdown']>;
  metricsJson: GenomeMetrics;
}

// ── Helpers ────────────────────────────────────────────────────────────

const dim = 'text-slate-500 dark:text-slate-400';

/** Color class for a proximity term value (green = passing, red = failing). */
function termColor(termValue: number): string {
  if (termValue > 0) return 'text-emerald-700 dark:text-emerald-400';
  if (termValue < 0) return 'text-rose-700 dark:text-rose-400';
  return 'text-slate-700 dark:text-slate-300';
}

/** Color class for a final fitness value (green > 0, red ≤ 0). */
function fitnessColor(v: number): string {
  return v > 0
    ? 'text-emerald-700 dark:text-emerald-400 font-semibold'
    : 'text-rose-700 dark:text-rose-400 font-semibold';
}

// ── Sub-components ─────────────────────────────────────────────────────

function TermRow({ term }: { term: ProximityTerm }) {
  const contribFormatted = fmtSigned(term.contribution, 4);
  const termFormatted = fmtSigned(term.termValue, 3);

  return (
    <div className="flex items-start gap-3 py-1.5">
      {/* Left: label + threshold + metric value */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-800 dark:text-slate-200 text-sm">
            {term.label}
          </span>
          <span className={`text-xs ${dim}`}>
            Gate B: {term.threshold}
          </span>
        </div>
        <div className={`text-xs ${dim} mt-0.5`}>
          Actual: <span className="font-mono">{term.metricFormatted}</span>
        </div>
      </div>

      {/* Right: term score, weight, contribution */}
      <div className="shrink-0 text-right font-mono text-xs space-y-0.5">
        <div className={termColor(term.termValue)}>
          term {termFormatted}
        </div>
        <div className={`${dim}`}>
          {term.weight.toFixed(2)} × {termFormatted} ={' '}
          <span className={termColor(term.contribution)}>
            {contribFormatted}
          </span>
        </div>
      </div>
    </div>
  );
}

function PenaltyRow({
  label,
  detail,
  value,
  isMultiplicative = false,
}: {
  label: string;
  detail: string;
  value: number;
  isMultiplicative?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5">
      <div className="min-w-0 flex-1">
        <div className="font-medium text-slate-800 dark:text-slate-200 text-sm">
          {isMultiplicative ? '' : '− '}
          {label}
        </div>
        <div className={`text-xs ${dim} mt-0.5`}>{detail}</div>
      </div>
      <div className="font-mono text-right shrink-0 w-28 text-sm">
        <span className="text-rose-700 dark:text-rose-400">
          {isMultiplicative ? `× ${value.toFixed(2)}` : fmtSigned(value)}
        </span>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────

export default function FitnessBreakdown({
  breakdown,
  metricsJson,
}: FitnessBreakdownProps) {
  const data = buildFitnessBreakdownData({
    metricsJson,
    penaltyBreakdown: breakdown,
  });

  return (
    <div className="space-y-2 text-sm">
      {/* Intro caption */}
      <p className={`text-xs ${dim} mb-3`}>
        The GA&apos;s fitness function rewards strategies that are close to
        passing all Gate B thresholds, weighted by the stated priority (CAGR
        weighted highest). Each term below shows how far the strategy is from
        its corresponding threshold: fitness near 0 means &ldquo;roughly
        passing&rdquo;, &gt;&nbsp;0 means exceeding, &lt;&nbsp;0 means failing.
      </p>

      {/* ── Top: 5 weighted proximity terms ───────────────────────── */}
      {data.terms !== null && (
        <div className="space-y-1 border border-slate-100 dark:border-slate-700 rounded-md px-3 py-2 mb-3 bg-slate-50 dark:bg-slate-900/40">
          <div className={`text-xs font-semibold uppercase tracking-wide ${dim} mb-1`}>
            Gate-B-proximity terms
          </div>
          {data.terms.map((term) => (
            <TermRow key={term.label} term={term} />
          ))}

          {/* Weighted sum subtotal */}
          <div className={`flex justify-between pt-1.5 border-t border-slate-200 dark:border-slate-700 text-xs font-mono ${dim}`}>
            <span>Weighted sum</span>
            <span className={termColor(data.terms.reduce((s, t) => s + t.contribution, 0))}>
              {fmtSigned(data.terms.reduce((s, t) => s + t.contribution, 0), 4)}
            </span>
          </div>
        </div>
      )}

      {/* ── Bottom: penalty chain ─────────────────────────────────── */}
      <div className="space-y-1">
        {/* Raw fitness = weighted sum − 0.5·std(fold Sharpe) */}
        <div className="flex items-start justify-between gap-3 py-1.5">
          <div className="min-w-0 flex-1">
            <div className="font-medium text-slate-800 dark:text-slate-200">
              Raw fitness
            </div>
            <div className={`text-xs ${dim} mt-0.5`}>
              Weighted Gate-B-proximity score: 0.35·CAGR + 0.25·MDD +
              0.20·Sharpe + 0.10·Hit + 0.10·Trades − 0.5·std(fold Sharpe).
              Each term is normalised so 0 = at threshold, +1 = double the
              threshold.
            </div>
          </div>
          <div className="font-mono text-right shrink-0 w-28">
            <span
              className={
                data.rawFitness < 0
                  ? 'text-rose-700 dark:text-rose-400'
                  : data.rawFitness > 0
                    ? 'text-emerald-700 dark:text-emerald-400'
                    : 'text-slate-700 dark:text-slate-300'
              }
            >
              {fmtSigned(data.rawFitness)}
            </span>
          </div>
        </div>

        {/* Zero-trade hard floor (only when active) */}
        {data.zeroTradePenaltyActive && (
          <PenaltyRow
            label="Zero-trade hard floor"
            detail="Flat −100 if the genome opened 0 trades on any training fold"
            value={breakdown.zero_trade_penalty}
          />
        )}

        {/* Diversity penalty (only when active) */}
        {data.diversityPenaltyActive && (
          <PenaltyRow
            label="Diversity penalty"
            detail="Halves fitness when the genome is within edit-distance 2 of an elite from this generation"
            value={breakdown.diversity_penalty_factor}
            isMultiplicative
          />
        )}
      </div>

      {/* Final fitness */}
      <div className="border-t border-slate-200 dark:border-slate-700 pt-3 mt-3 flex items-center justify-between font-mono text-sm">
        <span className="text-slate-700 dark:text-slate-200 font-semibold">
          Final fitness
        </span>
        <span className={fitnessColor(data.finalFitness)}>
          {data.finalFitness.toFixed(3)}
        </span>
      </div>
    </div>
  );
}
