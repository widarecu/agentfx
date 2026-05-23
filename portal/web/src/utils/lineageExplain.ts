/**
 * Lineage explainer — given a genome's origin + parents + DSL, decide:
 *
 *   - Which GA operator (or LLM, or seed) produced this genome?
 *   - In plain English, what was the operator trying to achieve?
 *   - For crossover: which DSL slot was inherited from which parent?
 *   - For mutation: which of the six mutation operators most likely fired,
 *     and which field did it touch?
 *
 * Detection from the *diff* between parent and child is heuristic but the
 * v0.3.0 DSL is small enough (6 indicator names, 6 comparators, 4 exit
 * fields) that the heuristic is unambiguous in practice. If the
 * heuristic can't pin an operator down, we fall back to "GA tweaked one
 * or more fields" rather than guessing.
 */

import type { DslSpec } from '../types/evolution';

// ─────────────────────────────────────────────────────────────────────
// Per-origin narratives
// ─────────────────────────────────────────────────────────────────────

export type OperatorKind =
  | 'seed'
  | 'warm_start'
  | 'llm'
  | 'crossover'
  | 'mutation-flip-comparator'
  | 'mutation-perturb-threshold'
  | 'mutation-swap-indicator'
  | 'mutation-change-window'
  | 'mutation-toggle-opposite-signal'
  | 'mutation-perturb-exit-numeric'
  | 'mutation-unknown';

export interface OperatorInfo {
  /** Short label, e.g. "perturb_threshold mutation" or "Crossover". */
  label: string;
  /** One-line plain-English description of what the operator does. */
  description: string;
  /** One-line description of what the GA "hopes" by applying it. */
  intent: string;
}

const OPERATOR_INFO: Record<OperatorKind, OperatorInfo> = {
  seed: {
    label: 'Canonical seed',
    description:
      'A hand-coded starter strategy (MA crossover, RSI mean-reversion, or Donchian breakout) re-expressed as DSL. Every run begins with these three.',
    intent:
      'Give the search a known-good starting point so the GA doesn\'t evolve from random noise.',
  },
  warm_start: {
    label: 'Warm-start (prior run)',
    description:
      'Imported into gen 0 from a previously completed run on the same instrument and granularity. parent_ids points at the source genome.',
    intent:
      'Carry forward the best result the platform has ever found for this pair/timeframe so each run inherits prior search effort instead of starting from scratch.',
  },
  llm: {
    label: 'LLM-proposed',
    description:
      'A novel strategy returned by the Anthropic LLM, given the current top genomes and fitness history as context.',
    intent:
      'Inject diversity that pure mutation/crossover can\'t reach — e.g. a different indicator family or a structurally new condition.',
  },
  crossover: {
    label: 'Crossover',
    description:
      'Single-slot recombination: the GA picked two parents and swapped exactly one of {long entry, short entry, exit rules} between them.',
    intent:
      'Combine the strongest parts of two parent strategies. Broader jumps than mutation, but constrained to material that already exists in the population.',
  },
  'mutation-flip-comparator': {
    label: 'Mutation — flip comparator',
    description:
      'Picked one comparison in the spec and flipped its operator (e.g. `<` → `<=`, or `crosses_above` → `crosses_below`).',
    intent:
      'Tiny semantic nudge — sometimes the difference between "enter on cross" and "stay in while above" is the difference between profitable and not.',
  },
  'mutation-perturb-threshold': {
    label: 'Mutation — perturb threshold',
    description:
      'Picked one numeric threshold (e.g. an RSI level) and scaled it by a random factor in [0.8, 1.2].',
    intent:
      'Local search around the parent\'s threshold values — most strategies are sensitive to these constants.',
  },
  'mutation-swap-indicator': {
    label: 'Mutation — swap indicator',
    description:
      'Replaced one indicator with another of the same arity (e.g. `sma` → `ema`, or `bb_upper` → `bb_lower`).',
    intent:
      'Test whether a different indicator family with the same shape produces a better signal on this instrument.',
  },
  'mutation-change-window': {
    label: 'Mutation — change window',
    description:
      'Picked one indicator and scaled its window parameter by a random factor in [0.75, 1.25] (e.g. sma(20) → sma(25)).',
    intent:
      'Sweep the lookback period — same indicator, different sensitivity to recent vs older data.',
  },
  'mutation-toggle-opposite-signal': {
    label: 'Mutation — toggle opposite-signal exit',
    description:
      'Flipped the `opposite_signal` exit flag on or off.',
    intent:
      'Test whether the strategy benefits from closing positions when its own counter-signal fires.',
  },
  'mutation-perturb-exit-numeric': {
    label: 'Mutation — perturb exit numeric',
    description:
      'Scaled one of stop_pips / target_pips / max_hold_bars by a random factor in [0.8, 1.2].',
    intent:
      'Tune the risk-management envelope — most strategies\' Sharpe is more sensitive to exit rules than entry rules.',
  },
  'mutation-unknown': {
    label: 'Mutation',
    description:
      'The GA tweaked one or more fields, but the diff doesn\'t match a single known mutation operator cleanly.',
    intent:
      'Local search around the parent. (This usually means an old run from a previous DSL version, or multiple mutations stacked.)',
  },
};

export function getOperatorInfo(kind: OperatorKind): OperatorInfo {
  return OPERATOR_INFO[kind];
}

// ─────────────────────────────────────────────────────────────────────
// Diff utilities
// ─────────────────────────────────────────────────────────────────────

type LeafPath = string[];

function flattenLeafs(
  obj: unknown,
  path: LeafPath,
  out: Map<string, unknown>,
): void {
  if (obj === null || obj === undefined || typeof obj !== 'object') {
    out.set(path.join('.'), obj);
    return;
  }
  if (Array.isArray(obj)) {
    obj.forEach((v, i) => flattenLeafs(v, [...path, `[${i}]`], out));
    return;
  }
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    flattenLeafs(v, [...path, k], out);
  }
}

export interface LeafDiff {
  path: string;
  before: unknown;
  after: unknown;
}

export function diffSpecs(a: DslSpec, b: DslSpec): LeafDiff[] {
  const A = new Map<string, unknown>();
  const B = new Map<string, unknown>();
  flattenLeafs(a, [], A);
  flattenLeafs(b, [], B);

  const allKeys = new Set([...A.keys(), ...B.keys()]);
  const diffs: LeafDiff[] = [];
  for (const k of allKeys) {
    const before = A.get(k);
    const after = B.get(k);
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      diffs.push({ path: k, before, after });
    }
  }
  return diffs;
}

// ─────────────────────────────────────────────────────────────────────
// Mutation operator detection
// ─────────────────────────────────────────────────────────────────────

/**
 * Inspect a parent→child diff and infer which mutation operator fired.
 *
 * Heuristics (deliberately strict; falls back to "unknown" when ambiguous):
 *   - all changes inside `cmp` keys → flip_comparator
 *   - all changes inside `ind` keys → swap_indicator
 *   - all changes inside `args[0]` keys → change_window
 *   - only `exit.opposite_signal` changed → toggle_opposite_signal
 *   - only `exit.{stop_pips,target_pips,max_hold_bars}` changed → perturb_exit_numeric
 *   - any other numeric scalar leaf change inside a comparison → perturb_threshold
 */
export function detectMutationOperator(diffs: LeafDiff[]): OperatorKind {
  if (diffs.length === 0) return 'mutation-unknown';

  const allCmp = diffs.every((d) => d.path.endsWith('.cmp'));
  if (allCmp) return 'mutation-flip-comparator';

  const allInd = diffs.every((d) => d.path.endsWith('.ind'));
  if (allInd) return 'mutation-swap-indicator';

  const allArg0 = diffs.every((d) => /\.args\.\[0\]$/.test(d.path));
  if (allArg0) return 'mutation-change-window';

  const onlyOpposite =
    diffs.length === 1 && diffs[0].path === 'exit.opposite_signal';
  if (onlyOpposite) return 'mutation-toggle-opposite-signal';

  const exitFields = new Set([
    'exit.stop_pips',
    'exit.target_pips',
    'exit.max_hold_bars',
  ]);
  const allExitNumeric = diffs.every((d) => exitFields.has(d.path));
  if (allExitNumeric) return 'mutation-perturb-exit-numeric';

  // Threshold perturbation: numeric leaves inside an entry comparison's
  // lhs or rhs (and not anywhere else)
  const allEntryThreshold = diffs.every((d) =>
    /^entry\.(long|short)\.(.*\.)?(lhs|rhs)$/.test(d.path),
  );
  if (allEntryThreshold && diffs.every((d) => typeof d.after === 'number')) {
    return 'mutation-perturb-threshold';
  }

  return 'mutation-unknown';
}

// ─────────────────────────────────────────────────────────────────────
// Crossover slot detection
// ─────────────────────────────────────────────────────────────────────

export type CrossoverSlot = 'long' | 'short' | 'exit';
export type SlotProvenance =
  | { slot: 'long'; from: string } // parent id
  | { slot: 'short'; from: string }
  | { slot: 'exit'; from: string }
  | { slot: 'size'; from: string };

/**
 * Decide which slot of the child's spec was swapped in by crossover,
 * and from which of the two parents.
 *
 * Given child C and parents A, B:
 *   - The crossover op swapped exactly ONE of {long, short, exit} between
 *     A and B. So C's spec equals A except for that one slot, which
 *     equals B's slot — or vice versa.
 *   - Inheritance is determined per-slot by deep-equality: for each
 *     slot, whichever parent's slot matches C's slot is the source.
 */
export function detectCrossoverInheritance(
  child: DslSpec,
  parents: DslSpec[],
  parentIds: string[],
): SlotProvenance[] {
  if (parents.length < 1) return [];
  const slots: ('long' | 'short' | 'exit' | 'size')[] = ['long', 'short', 'exit', 'size'];
  const out: SlotProvenance[] = [];

  for (const slot of slots) {
    const childSlot =
      slot === 'long' || slot === 'short'
        ? child.entry?.[slot]
        : (child as Record<string, unknown>)[slot];
    let sourceParentId: string | null = null;
    for (let i = 0; i < parents.length; i++) {
      const p = parents[i];
      const pSlot =
        slot === 'long' || slot === 'short'
          ? p.entry?.[slot]
          : (p as Record<string, unknown>)[slot];
      if (JSON.stringify(pSlot) === JSON.stringify(childSlot)) {
        sourceParentId = parentIds[i] ?? `parent-${i}`;
        break;
      }
    }
    if (sourceParentId) {
      // `size` exists only if it differs between parents — record only if relevant.
      if (slot === 'size' && parents.length > 1) {
        const allSizesEqual =
          JSON.stringify(parents[0].size) === JSON.stringify(parents[1].size);
        if (allSizesEqual) continue;
      }
      out.push({ slot, from: sourceParentId });
    }
  }

  return out;
}

export function findSwappedSlot(
  inheritance: SlotProvenance[],
): { slot: SlotProvenance['slot']; from: string } | null {
  // The swapped slot is the one whose source is the *minority* parent
  // (single-slot crossover means 1 slot comes from B, the other 2-3 from A).
  if (inheritance.length === 0) return null;
  const counts = new Map<string, number>();
  for (const s of inheritance) {
    counts.set(s.from, (counts.get(s.from) ?? 0) + 1);
  }
  // The minority parent — the one with the *fewer* slots — is the source of the swap.
  let minority: string | null = null;
  let minCount = Infinity;
  for (const [from, n] of counts) {
    if (n < minCount) {
      minCount = n;
      minority = from;
    }
  }
  if (minority == null) return null;
  const swapped = inheritance.find((s) => s.from === minority);
  if (!swapped) return null;
  return { slot: swapped.slot, from: minority };
}
