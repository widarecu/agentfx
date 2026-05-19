/**
 * Convert a Strategy DSL spec into a plain-English summary sentence.
 *
 * Used by:
 *   - GenomeDetail page hero — answers "what does this strategy *do*?"
 *   - GenomeLineageStrip tooltips — hover a parent to read its summary
 *
 * Deliberately simple: produces ~1 sentence, falls back to "unsupported
 * spec shape" if the input doesn't match the v1 grammar. The DSL is
 * small (6 indicators, 6 comparators) so a hand-rolled summariser is
 * the right size — no need for a general AST walker.
 */

import type { DslSpec, DslCondition, DslComparison, DslExpr } from '../types/evolution';

const COMPARATOR_PHRASES: Record<DslComparison['cmp'], string> = {
  '>': 'rises above',
  '<': 'falls below',
  '>=': 'is at or above',
  '<=': 'is at or below',
  crosses_above: 'crosses above',
  crosses_below: 'crosses below',
};

function summarizeExpr(expr: DslExpr): string {
  if (typeof expr === 'number') return expr.toString();
  if (typeof expr === 'object' && expr !== null && 'ind' in expr) {
    const ind = expr as { ind: string; args: number[] };
    if (ind.ind === 'close' || ind.args.length === 0) return ind.ind;
    return `${ind.ind}(${ind.args.join(', ')})`;
  }
  return JSON.stringify(expr);
}

function isComparison(cond: DslCondition): cond is DslComparison {
  return cond != null && typeof cond === 'object' && 'cmp' in cond;
}

function summarizeCondition(cond: DslCondition | null | undefined): string | null {
  if (!cond) return null;
  if (isComparison(cond)) {
    const phrase = COMPARATOR_PHRASES[cond.cmp] ?? cond.cmp;
    return `${summarizeExpr(cond.lhs)} ${phrase} ${summarizeExpr(cond.rhs)}`;
  }
  // Logical AND/OR
  if ('op' in cond && Array.isArray(cond.args)) {
    const parts = cond.args
      .map((a) => summarizeCondition(a))
      .filter((s): s is string => s != null);
    if (parts.length === 0) return null;
    const join = cond.op === 'OR' ? ' or ' : ' and ';
    return parts.length === 1 ? parts[0] : `(${parts.join(join)})`;
  }
  return null;
}

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Produce a 1–2 sentence summary describing what the strategy does on
 * each side (long/short) and how it exits.
 *
 * Returns the empty string for an empty/malformed spec.
 */
export function summarizeDsl(spec: DslSpec | null | undefined): string {
  if (!spec || typeof spec !== 'object') return '';

  const longCond = spec.entry?.long ?? null;
  const shortCond = spec.entry?.short ?? null;
  const longText = summarizeCondition(longCond);
  const shortText = summarizeCondition(shortCond);

  const entryParts: string[] = [];

  // Degenerate-genome detection: long and short conditions identical → no trades
  if (longCond && shortCond && deepEqual(longCond, shortCond)) {
    entryParts.push(
      `Degenerate: long and short conditions are identical (${longText ?? '—'}), so no trades fire.`,
    );
  } else {
    if (longText) entryParts.push(`Goes long when ${longText}.`);
    if (shortText) entryParts.push(`Goes short when ${shortText}.`);
  }

  const exit = spec.exit ?? {};
  const exitParts: string[] = [];
  if (typeof exit.stop_pips === 'number') exitParts.push(`${exit.stop_pips}-pip stop`);
  if (typeof exit.target_pips === 'number')
    exitParts.push(`${exit.target_pips}-pip target`);
  if (typeof exit.max_hold_bars === 'number')
    exitParts.push(`max ${exit.max_hold_bars}-bar hold`);
  if (exit.opposite_signal) exitParts.push('exits on opposite signal');

  const exitSentence =
    exitParts.length > 0 ? ` Exit: ${exitParts.join(', ')}.` : '';

  const risk = spec.size?.risk_pct;
  const riskSentence =
    typeof risk === 'number' ? ` Risks ${(risk * 100).toFixed(2)}% per trade.` : '';

  return [...entryParts, exitSentence.trim(), riskSentence.trim()]
    .filter(Boolean)
    .join(' ');
}

/**
 * Heuristic archetype label for a spec — used as a one-word badge.
 * Returns null when no archetype can be inferred.
 */
export function inferArchetype(
  spec: DslSpec | null | undefined,
): 'trend' | 'mean-reversion' | 'breakout' | 'mixed' | 'degenerate' | null {
  if (!spec?.entry) return null;
  const long = spec.entry.long;
  const short = spec.entry.short;
  if (long && short && deepEqual(long, short)) return 'degenerate';

  const indicatorsUsed = new Set<string>();
  function collect(cond: DslCondition | null | undefined): void {
    if (!cond) return;
    if (isComparison(cond)) {
      [cond.lhs, cond.rhs].forEach((expr) => {
        if (typeof expr === 'object' && expr != null && 'ind' in expr) {
          indicatorsUsed.add((expr as { ind: string }).ind);
        }
      });
    } else if ('args' in cond) {
      cond.args.forEach(collect);
    }
  }
  collect(long);
  collect(short);

  const inds = Array.from(indicatorsUsed);
  if (inds.length === 0) return null;
  const hasMomentum = inds.some((i) => i === 'rsi');
  const hasMA = inds.some((i) => i === 'sma' || i === 'ema');
  const hasBands = inds.some((i) => i.startsWith('bb_'));
  const hasDonchian = inds.some((i) => i.startsWith('donchian'));

  if (hasDonchian) return 'breakout';
  if (hasMA && long && short && isComparison(long) && isComparison(short)) {
    // MA-crossover heuristic: long uses '>' and short uses '<' (or vice versa) → trend
    const longCmp = (long as DslComparison).cmp;
    const shortCmp = (short as DslComparison).cmp;
    if (
      (longCmp === '>' && shortCmp === '<') ||
      (longCmp === 'crosses_above' && shortCmp === 'crosses_below')
    ) {
      return 'trend';
    }
    if (
      (longCmp === '<' && shortCmp === '>') ||
      (longCmp === '<=' && shortCmp === '>')
    ) {
      return 'mean-reversion';
    }
  }
  if (hasMomentum || hasBands) return 'mean-reversion';
  return 'mixed';
}
