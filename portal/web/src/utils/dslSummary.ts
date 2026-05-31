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
  // min_hold_bars only worth mentioning when non-zero — zero is the
  // legacy default ("any exit can fire on the next bar"), no signal.
  if (typeof exit.min_hold_bars === 'number' && exit.min_hold_bars > 0)
    exitParts.push(`min ${exit.min_hold_bars}-bar hold`);
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

export type ArchetypeKind =
  | 'trend'
  | 'mean-reversion'
  | 'breakout'
  | 'mixed'
  | 'degenerate';

/**
 * Heuristic archetype label for a spec — used as a one-word badge.
 * Returns null when no archetype can be inferred.
 */
export function inferArchetype(
  spec: DslSpec | null | undefined,
): ArchetypeKind | null {
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

// ─────────────────────────────────────────────────────────────────────
// Archetype tooltip content
// ─────────────────────────────────────────────────────────────────────

const ARCHETYPE_MEANING: Record<ArchetypeKind, string> = {
  trend:
    'Trend-following: enters in the direction of an established move; bets the trend continues.',
  'mean-reversion':
    'Mean-reverting: bets price returns to a moving average or oscillator midpoint; buys weakness, sells strength.',
  breakout:
    'Breakout: enters when price escapes a recent high/low channel.',
  mixed:
    "Mixed-signal: doesn't match trend / mean-reversion / breakout cleanly — couldn't classify with confidence.",
  degenerate:
    'Degenerate: long and short entry conditions are identical, so neither side ever fires exclusive of the other → no trades open. Caught by the fitness function\'s −100 zero-trade penalty.',
};

/**
 * Returns the specific reason the heuristic tagged a genome as the given
 * archetype, based on its DSL spec. Used in the tooltip alongside the
 * generic meaning so the user can see *why* this particular genome was
 * classified that way (and decide whether to trust the inference).
 */
export function archetypeReason(
  spec: DslSpec | null | undefined,
  archetype: ArchetypeKind,
): string {
  if (!spec?.entry) return '';
  const long = spec.entry.long;
  const short = spec.entry.short;

  if (archetype === 'degenerate') {
    return 'Long and short entry conditions are structurally identical (deepEqual check).';
  }

  // Collect indicator names used by the spec to mention them in the reason.
  const inds = new Set<string>();
  function collect(cond: DslCondition | null | undefined): void {
    if (!cond) return;
    if (isComparison(cond)) {
      [cond.lhs, cond.rhs].forEach((expr) => {
        if (typeof expr === 'object' && expr != null && 'ind' in expr) {
          inds.add((expr as { ind: string }).ind);
        }
      });
    } else if ('args' in cond) {
      cond.args.forEach(collect);
    }
  }
  collect(long);
  collect(short);

  if (archetype === 'breakout') {
    const donchian = Array.from(inds).filter((i) => i.startsWith('donchian'));
    return donchian.length > 0
      ? `Uses Donchian channel indicator(s): ${donchian.join(', ')}.`
      : 'Donchian-channel indicator detected.';
  }

  if (archetype === 'trend') {
    const ma = Array.from(inds).filter((i) => i === 'sma' || i === 'ema');
    const longCmp =
      long && isComparison(long) ? (long as DslComparison).cmp : '?';
    const shortCmp =
      short && isComparison(short) ? (short as DslComparison).cmp : '?';
    return `MA-family (${ma.join('/')}) with trend-shape sign convention: long uses ${longCmp}, short uses ${shortCmp}.`;
  }

  if (archetype === 'mean-reversion') {
    if (inds.has('rsi')) return 'Uses RSI — a momentum oscillator typically traded mean-reversion-style.';
    const bands = Array.from(inds).filter((i) => i.startsWith('bb_'));
    if (bands.length > 0)
      return `Uses Bollinger Bands (${bands.join(', ')}) — typically traded mean-reversion-style.`;
    const ma = Array.from(inds).filter((i) => i === 'sma' || i === 'ema');
    const longCmp =
      long && isComparison(long) ? (long as DslComparison).cmp : '?';
    const shortCmp =
      short && isComparison(short) ? (short as DslComparison).cmp : '?';
    return `MA-family (${ma.join('/')}) with INVERTED sign convention: long uses ${longCmp}, short uses ${shortCmp} (opposite of trend-following).`;
  }

  // mixed
  const indsArr = Array.from(inds);
  return indsArr.length > 0
    ? `Indicators ${indsArr.join('/')} used, but the comparator/structure combination doesn't fit trend, mean-reversion, or breakout.`
    : "Couldn't classify the spec's structure into a known archetype.";
}

/** Combined tooltip text for the archetype badge: meaning + heuristic. */
export function archetypeTooltip(
  spec: DslSpec | null | undefined,
  archetype: ArchetypeKind,
): string {
  const meaning = ARCHETYPE_MEANING[archetype];
  const reason = archetypeReason(spec, archetype);
  return reason ? `${meaning}\n\nTagged because: ${reason}` : meaning;
}
