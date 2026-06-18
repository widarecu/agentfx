/**
 * Per-bar parity between the JS DSL indicator implementations
 * (src/utils/dslIndicators.ts) and the Python reference implementations
 * in src/agentfx/core/dsl/indicators.py.
 *
 * The reference values come from scripts/dump_indicator_fixtures.py,
 * which runs the Python indicators on a deterministic synthetic OHLCV
 * series and writes the expected (ts, value) pairs to
 * tests/fixtures/indicator-values.json. Re-generate the fixture
 * whenever any indicator's Python semantics change.
 */

import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { buildIndicatorsFromDsl } from '../../src/utils/dslIndicators';
import type { CandleData } from '../../src/types/optimization';
import type { DslSpec } from '../../src/types/evolution';

interface FixtureBar {
  ts: string;
  value: number;
}

interface Fixture {
  candles: CandleData[];
  indicators: Record<string, FixtureBar[]>;
}

const fixturePath = resolve(__dirname, '../fixtures/indicator-values.json');
const fixture = JSON.parse(readFileSync(fixturePath, 'utf-8')) as Fixture;

/**
 * Build a synthetic DSL spec that references exactly one indicator. The
 * spec only needs to be valid enough that `collectIndicatorCalls` picks
 * up the indicator — entry/exit/size semantics are irrelevant to the
 * computed indicator series.
 */
function singleIndicatorSpec(name: string, args: number[]): DslSpec {
  return {
    version: 1,
    entry: {
      long: {
        lhs: { ind: name, args },
        cmp: '>',
        rhs: 0,
      },
    },
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

// (label-in-fixture, indicator-name, args). Label matches what
// `labelFor()` produces in dslIndicators.ts — `${name}(${args.join(', ')})`
// — or just the name for zero-arg indicators (obv).
const CASES: Array<[string, string, number[]]> = [
  ['sma(10)', 'sma', [10]],
  ['sma(50)', 'sma', [50]],
  ['ema(12)', 'ema', [12]],
  ['ema(26)', 'ema', [26]],
  ['rsi(14)', 'rsi', [14]],
  ['atr(14)', 'atr', [14]],
  ['bb_upper(20, 2)', 'bb_upper', [20, 2.0]],
  ['bb_lower(20, 2)', 'bb_lower', [20, 2.0]],
  ['donchian_high(20)', 'donchian_high', [20]],
  ['donchian_low(20)', 'donchian_low', [20]],
  ['macd_line(12, 26)', 'macd_line', [12, 26]],
  ['macd_line(5, 20)', 'macd_line', [5, 20]],
  ['macd_signal(12, 26, 9)', 'macd_signal', [12, 26, 9]],
  ['stoch(14, 3)', 'stoch', [14, 3]],
  ['stoch(5, 1)', 'stoch', [5, 1]],
  ['adx(14)', 'adx', [14]],
  ['adx(7)', 'adx', [7]],
  ['obv', 'obv', []],
  ['ichimoku_tenkan(9)', 'ichimoku_tenkan', [9]],
  ['ichimoku_kijun(26)', 'ichimoku_kijun', [26]],
];

// Tolerances are intentionally loose enough that float-order-of-operations
// differences between numpy/pandas and the JS impls don't trip the test,
// while still being tight enough to catch real semantic bugs (a wrong
// window, a wrong smoothing constant, a wrong NaN handling).
//
// rel = relative tolerance on the value; abs = absolute floor so a tiny
// nonzero expected value doesn't blow up the relative comparison.
//
// ADX uses a slightly looser bound because Wilder smoothing chains four
// EWMs deep and accumulates float drift across them.
const DEFAULT_TOL = { rel: 1e-9, abs: 1e-9 };
const PER_INDICATOR_TOL: Record<string, { rel: number; abs: number }> = {
  adx: { rel: 1e-6, abs: 1e-6 },
};

describe('JS↔Python indicator parity', () => {
  for (const [label, name, args] of CASES) {
    it(`${label} matches Python bar-by-bar`, () => {
      const spec = singleIndicatorSpec(name, args);
      const lines = buildIndicatorsFromDsl(spec, fixture.candles);
      expect(lines.length, `no JS series produced for ${label}`).toBe(1);
      const actual = lines[0].data;
      const expected = fixture.indicators[label];

      // Length parity catches "JS dropped/added bars relative to Python"
      // — the most common subtle bug shape (NaN-window mismatch, off-by-one
      // initialisation, wrong min_periods).
      expect(actual.length, `length mismatch for ${label}`).toBe(expected.length);

      const tol = PER_INDICATOR_TOL[name] ?? DEFAULT_TOL;
      for (let i = 0; i < expected.length; i++) {
        const e = expected[i];
        const a = actual[i];
        expect(a.timestamp, `${label} bar ${i} ts`).toBe(e.ts);
        const diff = Math.abs(a.value - e.value);
        const bound = Math.max(tol.abs, tol.rel * Math.abs(e.value));
        expect(
          diff,
          `${label} bar ${i} ts=${e.ts}: JS=${a.value}, Python=${e.value}, diff=${diff} > bound=${bound}`,
        ).toBeLessThanOrEqual(bound);
      }
    });
  }
});
