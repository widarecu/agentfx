/**
 * Archetype classifier + plain-English summary behaviour, focusing on
 * the v0.4.0 indicator expansion (MACD, stochastic, ADX, OBV, Ichimoku).
 *
 * The classifier is the heuristic that picks the badge shown on the
 * GenomeDetail hero. Before v0.4.0 it only knew about RSI / SMA / EMA /
 * Bollinger / Donchian; this suite asserts the new indicators are routed
 * to the correct archetype family and that the human-readable "why this
 * was tagged" reason picks them up.
 */

import { describe, expect, it } from 'vitest';

import {
  inferArchetype,
  summarizeDsl,
  archetypeReason,
} from '../../src/utils/dslSummary';
import type { DslSpec } from '../../src/types/evolution';

function buildSpec(
  long: unknown | null,
  short: unknown | null,
): DslSpec {
  return {
    version: 1,
    entry: { long, short },
    exit: {
      stop_pips: 10,
      target_pips: 20,
      max_hold_bars: 50,
      min_hold_bars: 0,
      opposite_signal: true,
    },
    size: { risk_pct: 0.001 },
  } as DslSpec;
}

function ind(name: string, args: number[] = []) {
  return { ind: name, args };
}

describe('inferArchetype — v0.4.0 indicator expansion', () => {
  it('classifies stoch oversold/overbought as mean-reversion', () => {
    const spec = buildSpec(
      { lhs: ind('stoch', [14, 3]), cmp: '<', rhs: 20 },
      { lhs: ind('stoch', [14, 3]), cmp: '>', rhs: 80 },
    );
    expect(inferArchetype(spec)).toBe('mean-reversion');
    expect(archetypeReason(spec, 'mean-reversion')).toMatch(/stoch/);
  });

  it('classifies adx threshold strategies as mean-reversion (bounded oscillator)', () => {
    // ADX is an oscillator family member even though it's typically used
    // as a trend filter — the heuristic groups by scale (bounded) rather
    // than by user intent. This is the correct fallthrough today;
    // re-evaluate when a dedicated "filter" archetype is added.
    const spec = buildSpec(
      { lhs: ind('adx', [14]), cmp: '>', rhs: 25 },
      { lhs: ind('adx', [14]), cmp: '<', rhs: 20 },
    );
    expect(inferArchetype(spec)).toBe('mean-reversion');
    expect(archetypeReason(spec, 'mean-reversion')).toMatch(/adx/);
  });

  it('classifies MACD line crossing signal as trend-following', () => {
    const spec = buildSpec(
      {
        lhs: ind('macd_line', [12, 26]),
        cmp: 'crosses_above',
        rhs: ind('macd_signal', [12, 26, 9]),
      },
      {
        lhs: ind('macd_line', [12, 26]),
        cmp: 'crosses_below',
        rhs: ind('macd_signal', [12, 26, 9]),
      },
    );
    expect(inferArchetype(spec)).toBe('trend');
    expect(archetypeReason(spec, 'trend')).toMatch(/macd_line/);
  });

  it('classifies Ichimoku tenkan/kijun crossover as breakout (channel family)', () => {
    const spec = buildSpec(
      {
        lhs: ind('ichimoku_tenkan', [9]),
        cmp: 'crosses_above',
        rhs: ind('ichimoku_kijun', [26]),
      },
      {
        lhs: ind('ichimoku_tenkan', [9]),
        cmp: 'crosses_below',
        rhs: ind('ichimoku_kijun', [26]),
      },
    );
    expect(inferArchetype(spec)).toBe('breakout');
    const reason = archetypeReason(spec, 'breakout');
    expect(reason).toMatch(/ichimoku/);
  });

  it('classifies OBV-only specs as mixed (no archetype counterpart yet)', () => {
    const spec = buildSpec(
      { lhs: ind('obv'), cmp: '>', rhs: 0 },
      { lhs: ind('obv'), cmp: '<', rhs: 0 },
    );
    // OBV doesn't fit any current archetype — confirm it doesn't get
    // mis-classified into one of the existing buckets.
    expect(inferArchetype(spec)).toBe('mixed');
  });

  it('regression: existing RSI mean-reversion still classifies as mean-reversion', () => {
    const spec = buildSpec(
      { lhs: ind('rsi', [14]), cmp: '<', rhs: 30 },
      { lhs: ind('rsi', [14]), cmp: '>', rhs: 70 },
    );
    expect(inferArchetype(spec)).toBe('mean-reversion');
    expect(archetypeReason(spec, 'mean-reversion')).toMatch(/rsi/);
  });

  it('regression: existing SMA crossover still classifies as trend', () => {
    const spec = buildSpec(
      { lhs: ind('sma', [10]), cmp: 'crosses_above', rhs: ind('sma', [50]) },
      { lhs: ind('sma', [10]), cmp: 'crosses_below', rhs: ind('sma', [50]) },
    );
    expect(inferArchetype(spec)).toBe('trend');
  });

  it('regression: existing Donchian breakout still classifies as breakout', () => {
    const spec = buildSpec(
      { lhs: ind('donchian_high', [20]), cmp: '>', rhs: ind('donchian_low', [20]) },
      null,
    );
    expect(inferArchetype(spec)).toBe('breakout');
  });

  it('degenerate detection still fires for identical long/short conditions', () => {
    const cond = { lhs: ind('macd_line', [12, 26]), cmp: '>', rhs: 0 };
    const spec = buildSpec(cond, cond);
    expect(inferArchetype(spec)).toBe('degenerate');
  });
});

describe('summarizeDsl — "stays while" vs "goes when"', () => {
  // State comparators (>, <, >=, <=) hold across many bars — the strategy
  // STAYS in position while the condition is true. Crossover comparators
  // are one-shot events — the strategy GOES into position when they fire.
  // The verb in the rendered summary should reflect which one applies so
  // the user reads "stays long while OBV > 0" (correct: continuous state)
  // rather than "goes long when OBV > 0" (misleading: sounds eventful).
  it('renders state-comparator long as "Stays long while ..."', () => {
    const spec = buildSpec(
      { lhs: { ind: 'obv', args: [] }, cmp: '>', rhs: 0 },
      null,
    );
    expect(summarizeDsl(spec)).toMatch(/Stays long while obv is above 0/);
    expect(summarizeDsl(spec)).not.toMatch(/Goes long when/);
  });

  it('renders state-comparator short as "Stays short while ..."', () => {
    const spec = buildSpec(
      null,
      { lhs: { ind: 'rsi', args: [14] }, cmp: '>', rhs: 70 },
    );
    expect(summarizeDsl(spec)).toMatch(/Stays short while rsi\(14\) is above 70/);
  });

  it('renders crossover-comparator long as "Goes long when ..."', () => {
    // crosses_above IS a one-shot event so "goes long when" is correct
    // — no regression to "stays long while" for crossovers.
    const spec = buildSpec(
      {
        lhs: { ind: 'macd_line', args: [12, 26] },
        cmp: 'crosses_above',
        rhs: { ind: 'macd_signal', args: [12, 26, 9] },
      },
      null,
    );
    const summary = summarizeDsl(spec);
    expect(summary).toMatch(/Goes long when macd_line\(12, 26\) crosses above/);
    expect(summary).not.toMatch(/Stays long while/);
  });
});

describe('summarizeDsl — state vs event semantics', () => {
  // Before 2026-06-17 '>' / '<' rendered as "rises above" / "falls
  // below" which both imply transitions (one bar where the inequality
  // first becomes true). The actual operator is a STATE check (every
  // bar where the inequality holds). The misleading wording made the
  // user read the OBV seed's summary as "shorts on every zero-cross"
  // when actually it's "long while OBV > 0, short while OBV < 0".
  it('renders > as a state ("is above"), not a transition', () => {
    const spec = buildSpec(
      { lhs: { ind: 'obv', args: [] }, cmp: '>', rhs: 0 },
      null,
    );
    expect(summarizeDsl(spec)).toMatch(/obv is above 0/);
    expect(summarizeDsl(spec)).not.toMatch(/rises above/);
  });

  it('renders < as a state ("is below"), not a transition', () => {
    const spec = buildSpec(
      null,
      { lhs: { ind: 'obv', args: [] }, cmp: '<', rhs: 0 },
    );
    expect(summarizeDsl(spec)).toMatch(/obv is below 0/);
    expect(summarizeDsl(spec)).not.toMatch(/falls below/);
  });

  it('keeps crosses_above as a transition phrase', () => {
    // crosses_above IS a transition event (lhs[t-1] <= rhs[t-1] and
    // lhs[t] > rhs[t]) — the wording correctly reflects that.
    const spec = buildSpec(
      {
        lhs: { ind: 'macd_line', args: [12, 26] },
        cmp: 'crosses_above',
        rhs: { ind: 'macd_signal', args: [12, 26, 9] },
      },
      null,
    );
    expect(summarizeDsl(spec)).toMatch(/crosses above/);
  });
});

describe('summarizeDsl — defensive against missing args', () => {
  // The Pydantic backend always serialises zero-arg indicators with
  // `args: []`, but hand-authored JSON literals (in the chart gallery
  // page, in tests, in docs) sometimes omit the field. The summariser
  // must not crash on that shape — the gallery did, with
  // `Cannot read properties of undefined (reading 'length')`, on
  // 2026-06-17 when viewing the BB/Donchian/OBV seed sections.
  it('does not crash when an indicator omits the args field', () => {
    const spec = buildSpec(
      { lhs: { ind: 'obv' } as unknown as ReturnType<typeof ind>, cmp: '>', rhs: 0 },
      null,
    );
    // Should render "obv" (the zero-arg case), not throw.
    expect(summarizeDsl(spec)).toMatch(/\bobv\b/);
  });

  it('does not crash when close is referenced without an args field', () => {
    const spec = buildSpec(
      {
        lhs: { ind: 'close' } as unknown as ReturnType<typeof ind>,
        cmp: '>',
        rhs: { ind: 'donchian_high', args: [20] },
      },
      null,
    );
    expect(summarizeDsl(spec)).toMatch(/close.*donchian_high\(20\)/);
  });
});

describe('summarizeDsl — v0.4.0 indicators render in plain English', () => {
  it('renders MACD line/signal crossover', () => {
    const spec = buildSpec(
      {
        lhs: ind('macd_line', [12, 26]),
        cmp: 'crosses_above',
        rhs: ind('macd_signal', [12, 26, 9]),
      },
      null,
    );
    const summary = summarizeDsl(spec);
    expect(summary).toMatch(/macd_line\(12, 26\)/);
    expect(summary).toMatch(/crosses above/);
    expect(summary).toMatch(/macd_signal\(12, 26, 9\)/);
  });

  it('renders OBV with no args (zero-arg form)', () => {
    const spec = buildSpec({ lhs: ind('obv'), cmp: '>', rhs: 0 }, null);
    const summary = summarizeDsl(spec);
    // Zero-arg indicators render without an empty `()` per summarizeExpr's
    // contract — confirm we don't get "obv()" hanging in the sentence.
    expect(summary).toMatch(/\bobv\b/);
    expect(summary).not.toMatch(/obv\(\s*\)/);
  });

  it('renders stoch with two args', () => {
    const spec = buildSpec(
      { lhs: ind('stoch', [14, 3]), cmp: '<', rhs: 20 },
      null,
    );
    expect(summarizeDsl(spec)).toMatch(/stoch\(14, 3\) is below 20/);
  });

  it('renders ichimoku tenkan/kijun cross with explicit windows', () => {
    const spec = buildSpec(
      {
        lhs: ind('ichimoku_tenkan', [9]),
        cmp: 'crosses_above',
        rhs: ind('ichimoku_kijun', [26]),
      },
      null,
    );
    const summary = summarizeDsl(spec);
    expect(summary).toMatch(/ichimoku_tenkan\(9\)/);
    expect(summary).toMatch(/ichimoku_kijun\(26\)/);
  });
});
