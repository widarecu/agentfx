/**
 * Vitest for the Gate B evaluators in `types/paper.ts` (US-021 / PR-I).
 *
 * The evaluators are pure — kept out of the ApprovalQueueCard
 * component so this test file doesn't need jsdom. Locks the boundary
 * conditions the wireframe's colour coding depends on.
 */

import { describe, expect, it } from 'vitest';
import {
  clearsGateB,
  evaluateHitRate,
  evaluateMaxDrawdown,
  evaluateSharpe,
  evaluateTrades,
  GATE_B_THRESHOLDS,
} from '../../src/types/paper';

describe('evaluateSharpe', () => {
  it('fails below the threshold', () => {
    expect(evaluateSharpe(0.5)).toBe('fail');
    expect(evaluateSharpe(0.99)).toBe('fail');
  });

  it('marks borderline within 10% above threshold', () => {
    expect(evaluateSharpe(1.0)).toBe('borderline');
    expect(evaluateSharpe(1.05)).toBe('borderline');
    expect(evaluateSharpe(1.09)).toBe('borderline');
  });

  it('passes at ≥10% above threshold', () => {
    expect(evaluateSharpe(1.1)).toBe('pass');
    expect(evaluateSharpe(1.5)).toBe('pass');
  });

  it('nullish → fail (fail-closed)', () => {
    expect(evaluateSharpe(null)).toBe('fail');
    expect(evaluateSharpe(undefined)).toBe('fail');
  });
});

describe('evaluateMaxDrawdown', () => {
  it('accepts both negative and positive representations', () => {
    // Backend stores drawdown as a negative decimal, some UIs show
    // it as absolute. Helper handles both.
    expect(evaluateMaxDrawdown(-0.05)).toBe('pass');
    expect(evaluateMaxDrawdown(0.05)).toBe('pass');
  });

  it('fails past the 10% cap', () => {
    expect(evaluateMaxDrawdown(-0.11)).toBe('fail');
    expect(evaluateMaxDrawdown(0.15)).toBe('fail');
  });

  it('marks borderline above 9% (within 10% of cap)', () => {
    expect(evaluateMaxDrawdown(-0.091)).toBe('borderline');
  });

  it('null → fail', () => {
    expect(evaluateMaxDrawdown(null)).toBe('fail');
  });
});

describe('evaluateHitRate', () => {
  it('fails below 48%', () => {
    expect(evaluateHitRate(0.47)).toBe('fail');
  });

  it('borderline at threshold', () => {
    expect(evaluateHitRate(0.48)).toBe('borderline');
  });

  it('borderline within 5%', () => {
    expect(evaluateHitRate(0.5)).toBe('borderline');
  });

  it('passes ≥ 5% above threshold', () => {
    // 0.48 * 1.05 = 0.504 → 0.51 clears
    expect(evaluateHitRate(0.51)).toBe('pass');
  });
});

describe('evaluateTrades', () => {
  it('fails below 300', () => {
    expect(evaluateTrades(299)).toBe('fail');
    expect(evaluateTrades(0)).toBe('fail');
  });

  it('borderline within 5% of threshold', () => {
    expect(evaluateTrades(300)).toBe('borderline');
    expect(evaluateTrades(314)).toBe('borderline');
  });

  it('passes at 315+ (5% headroom)', () => {
    // 300 * 1.05 = 315 exactly
    expect(evaluateTrades(315)).toBe('pass');
    expect(evaluateTrades(1000)).toBe('pass');
  });
});

describe('clearsGateB (aggregate)', () => {
  it('true when every metric is at least borderline', () => {
    expect(
      clearsGateB({
        sharpe: 1.0,
        max_drawdown: -0.05,
        hit_rate: 0.48,
        trades: 300,
      }),
    ).toBe(true);
  });

  it('false when any metric fails', () => {
    expect(
      clearsGateB({
        sharpe: 0.8,
        max_drawdown: -0.05,
        hit_rate: 0.48,
        trades: 300,
      }),
    ).toBe(false);
  });

  it('accepts `n_trades` as an alternate key', () => {
    expect(
      clearsGateB({
        sharpe: 1.5,
        max_drawdown: -0.05,
        hit_rate: 0.55,
        n_trades: 500,
      }),
    ).toBe(true);
  });

  it('nullish metrics fail', () => {
    expect(clearsGateB(null)).toBe(false);
    expect(clearsGateB(undefined)).toBe(false);
    expect(clearsGateB({})).toBe(false);
  });
});

describe('GATE_B_THRESHOLDS constants', () => {
  // Locks the actual numbers against the backend's plan/CLAUDE.md.
  // Not a "test" of behaviour so much as a canary — if these change,
  // a downstream metric widget probably needs updating too.
  it('mirrors the plan', () => {
    expect(GATE_B_THRESHOLDS.sharpe_min).toBe(1.0);
    expect(GATE_B_THRESHOLDS.max_drawdown_max).toBe(0.1);
    expect(GATE_B_THRESHOLDS.hit_rate_min).toBe(0.48);
    expect(GATE_B_THRESHOLDS.trades_min).toBe(300);
  });
});
