/**
 * Unit tests for the Gate-B-proximity fitness breakdown logic.
 *
 * We test the pure-computation layer in `src/utils/fitnessBreakdown.ts`
 * rather than the React component directly — Vitest runs in the `node`
 * environment (no DOM / jsdom configured), and the project has no
 * @testing-library/react in devDependencies, so DOM rendering tests
 * are not available in this suite.
 *
 * Coverage goals (per spec):
 *   - buildProximityTerms produces 5 rows with correct contributions
 *     (weight × term value) and correct `passing` flags.
 *   - Weighted sum of contributions equals what arithmetic says it should.
 *   - buildFitnessBreakdownData returns null `terms` when gate_b_proximity_terms
 *     is absent (graceful legacy-genome handling).
 *   - zeroTradePenaltyActive is true only when penalty !== 0.
 *   - diversityPenaltyActive is true only when factor !== 1.0.
 *   - finalFitness round-trips from penaltyBreakdown correctly.
 *   - rawFitness prefers metrics_json.raw_fitness over breakdown.raw_fitness.
 *   - Final fitness color convention: positive = "green-passing", ≤ 0 = "red-failing"
 *     (verified via sign of finalFitness in the data object).
 */

import { describe, expect, it } from 'vitest';
import {
  buildProximityTerms,
  buildFitnessBreakdownData,
  fmtSigned,
  fmtPct,
  fmtNum,
  DEFAULT_WEIGHTS,
} from '../../src/utils/fitnessBreakdown';

// ── Shared fixtures ────────────────────────────────────────────────────

/** A genome exceeding all Gate B thresholds */
const PASSING_TERMS = {
  cagr_term: 0.40,    // CAGR above threshold
  mdd_term: 0.50,     // MDD better than threshold
  sharpe_term: 0.60,  // Sharpe above threshold
  hit_term: 0.20,     // Hit rate above threshold
  trades_term: 0.30,  // Trade count above threshold
};

/** A genome failing all Gate B thresholds */
const FAILING_TERMS = {
  cagr_term: -0.80,
  mdd_term: -0.40,
  sharpe_term: -1.00,
  hit_term: -0.50,
  trades_term: -0.60,
};

const PASSING_METRICS = {
  cagr: 0.25,          // 25% — above 15% threshold
  max_drawdown: 0.05,  // 5% — below 10% threshold
  sharpe: 0.90,        // above 0.5 threshold
  hit_rate: 0.52,      // 52% — above 45% threshold
  n_trades: 45,        // above 30 threshold
};

const BASELINE_PENALTY_BREAKDOWN = {
  raw_fitness: 0.35,
  zero_trade_penalty: 0,
  trade_shortfall_penalty: 0,   // no-op in v0.6.0
  drawdown_breach_penalty: 0,   // no-op in v0.6.0
  gate_b_dilution_factor: 1.0,  // no-op in v0.6.0
  diversity_penalty_factor: 1.0,
  final_fitness: 0.35,
};

// ── fmtSigned ─────────────────────────────────────────────────────────

describe('fmtSigned', () => {
  it('prefixes positive numbers with +', () => {
    expect(fmtSigned(0.123, 3)).toBe('+0.123');
  });

  it('does not add a + prefix for negative numbers', () => {
    expect(fmtSigned(-0.456, 3)).toBe('-0.456');
  });

  it('returns — for non-finite values', () => {
    expect(fmtSigned(Infinity)).toBe('—');
    expect(fmtSigned(NaN)).toBe('—');
  });

  it('respects the digits argument', () => {
    expect(fmtSigned(1.23456, 2)).toBe('+1.23');
  });
});

// ── fmtPct ────────────────────────────────────────────────────────────

describe('fmtPct', () => {
  it('converts a decimal fraction to a percentage string', () => {
    expect(fmtPct(0.153)).toBe('15.3%');
  });

  it('returns — for null', () => {
    expect(fmtPct(null)).toBe('—');
  });

  it('returns — for undefined', () => {
    expect(fmtPct(undefined)).toBe('—');
  });

  it('returns — for NaN', () => {
    expect(fmtPct(NaN)).toBe('—');
  });

  it('respects the digits argument', () => {
    expect(fmtPct(0.1234, 2)).toBe('12.34%');
  });
});

// ── fmtNum ────────────────────────────────────────────────────────────

describe('fmtNum', () => {
  it('formats a number to the given decimal places', () => {
    expect(fmtNum(1.2345, 2)).toBe('1.23');
  });

  it('returns — for null', () => {
    expect(fmtNum(null)).toBe('—');
  });
});

// ── buildProximityTerms ───────────────────────────────────────────────

describe('buildProximityTerms — five rows with correct structure', () => {
  it('returns exactly 5 terms in the canonical order', () => {
    const terms = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    expect(terms).toHaveLength(5);
    const labels = terms.map((t) => t.label);
    expect(labels).toEqual(['CAGR', 'Max Drawdown', 'Sharpe', 'Hit Rate', 'Trades']);
  });

  it('computes contribution = weight × termValue for each term', () => {
    const terms = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    for (const term of terms) {
      expect(term.contribution).toBeCloseTo(term.weight * term.termValue, 10);
    }
  });

  it('uses the default weights matching the backend v0.6.0 spec', () => {
    const terms = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    const [cagr, mdd, sharpe, hit, trades] = terms;
    expect(cagr.weight).toBe(DEFAULT_WEIGHTS.cagr);    // 0.35
    expect(mdd.weight).toBe(DEFAULT_WEIGHTS.mdd);      // 0.25
    expect(sharpe.weight).toBe(DEFAULT_WEIGHTS.sharpe); // 0.20
    expect(hit.weight).toBe(DEFAULT_WEIGHTS.hit);      // 0.10
    expect(trades.weight).toBe(DEFAULT_WEIGHTS.trades); // 0.10
  });

  it('sets passing=true when term value is positive (exceeding threshold)', () => {
    const terms = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    for (const term of terms) {
      expect(term.passing).toBe(true);
    }
  });

  it('sets passing=false when term value is negative (failing threshold)', () => {
    const terms = buildProximityTerms(FAILING_TERMS, PASSING_METRICS);
    for (const term of terms) {
      expect(term.passing).toBe(false);
    }
  });

  it('passes term=0 through as passing=true (exactly at threshold)', () => {
    const atThreshold = {
      cagr_term: 0,
      mdd_term: 0,
      sharpe_term: 0,
      hit_term: 0,
      trades_term: 0,
    };
    const terms = buildProximityTerms(atThreshold, PASSING_METRICS);
    for (const term of terms) {
      expect(term.passing).toBe(true);
    }
  });

  it('CAGR term shows threshold ≥ 15%', () => {
    const [cagr] = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    expect(cagr.threshold).toBe('≥ 15%');
  });

  it('MDD term shows threshold ≤ 10%', () => {
    const [, mdd] = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    expect(mdd.threshold).toBe('≤ 10%');
  });

  it('Sharpe term shows threshold ≥ 0.5', () => {
    const [, , sharpe] = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    expect(sharpe.threshold).toBe('≥ 0.5');
  });

  it('Hit Rate term shows threshold ≥ 45%', () => {
    const [, , , hit] = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    expect(hit.threshold).toBe('≥ 45%');
  });

  it('Trades term shows threshold ≥ 30', () => {
    const [, , , , trades] = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    expect(trades.threshold).toBe('≥ 30');
  });

  it('formats trade count as an integer string (no decimal)', () => {
    const [, , , , trades] = buildProximityTerms(PASSING_TERMS, { n_trades: 45 });
    expect(trades.metricFormatted).toBe('45');
  });

  it('formats CAGR as a percentage string', () => {
    const [cagr] = buildProximityTerms(PASSING_TERMS, { cagr: 0.25 });
    expect(cagr.metricFormatted).toBe('25.0%');
  });

  it('formats MDD as a percentage string', () => {
    const [, mdd] = buildProximityTerms(PASSING_TERMS, { max_drawdown: 0.07 });
    expect(mdd.metricFormatted).toBe('7.0%');
  });

  it('formats Sharpe as a decimal', () => {
    const [, , sharpe] = buildProximityTerms(PASSING_TERMS, { sharpe: 0.90 });
    expect(sharpe.metricFormatted).toBe('0.90');
  });

  it('renders — for a missing metric value', () => {
    const [cagr] = buildProximityTerms(PASSING_TERMS, { cagr: null });
    expect(cagr.metricFormatted).toBe('—');
    expect(cagr.metricValue).toBeNull();
  });

  it('accepts custom weights and applies them correctly', () => {
    const custom = { cagr: 0.50, mdd: 0.20, sharpe: 0.15, hit: 0.10, trades: 0.05 };
    const terms = buildProximityTerms(PASSING_TERMS, PASSING_METRICS, custom);
    const [cagr] = terms;
    expect(cagr.weight).toBe(0.50);
    expect(cagr.contribution).toBeCloseTo(0.50 * PASSING_TERMS.cagr_term, 10);
  });

  it('weighted sum of all contributions matches manual calculation', () => {
    const terms = buildProximityTerms(PASSING_TERMS, PASSING_METRICS);
    const weightedSum = terms.reduce((s, t) => s + t.contribution, 0);
    const expected =
      DEFAULT_WEIGHTS.cagr * PASSING_TERMS.cagr_term +
      DEFAULT_WEIGHTS.mdd * PASSING_TERMS.mdd_term +
      DEFAULT_WEIGHTS.sharpe * PASSING_TERMS.sharpe_term +
      DEFAULT_WEIGHTS.hit * PASSING_TERMS.hit_term +
      DEFAULT_WEIGHTS.trades * PASSING_TERMS.trades_term;
    expect(weightedSum).toBeCloseTo(expected, 10);
  });
});

// ── buildFitnessBreakdownData ─────────────────────────────────────────

describe('buildFitnessBreakdownData — v0.6.0 genome (gate_b_proximity_terms present)', () => {
  const metricsJson = {
    ...PASSING_METRICS,
    raw_fitness: 0.42,
    gate_b_proximity_terms: PASSING_TERMS,
  };

  it('returns 5 terms when gate_b_proximity_terms is present', () => {
    const data = buildFitnessBreakdownData({
      metricsJson,
      penaltyBreakdown: BASELINE_PENALTY_BREAKDOWN,
    });
    expect(data.terms).not.toBeNull();
    expect(data.terms!).toHaveLength(5);
  });

  it('uses metrics_json.raw_fitness when available (preferred over breakdown)', () => {
    const data = buildFitnessBreakdownData({
      metricsJson,
      penaltyBreakdown: { ...BASELINE_PENALTY_BREAKDOWN, raw_fitness: 0.99 },
    });
    // metrics_json.raw_fitness (0.42) should win over breakdown.raw_fitness (0.99)
    expect(data.rawFitness).toBe(0.42);
  });

  it('finalFitness comes from penaltyBreakdown.final_fitness', () => {
    const data = buildFitnessBreakdownData({
      metricsJson,
      penaltyBreakdown: { ...BASELINE_PENALTY_BREAKDOWN, final_fitness: 0.21 },
    });
    expect(data.finalFitness).toBe(0.21);
  });

  it('zeroTradePenaltyActive is false when penalty is 0', () => {
    const data = buildFitnessBreakdownData({
      metricsJson,
      penaltyBreakdown: { ...BASELINE_PENALTY_BREAKDOWN, zero_trade_penalty: 0 },
    });
    expect(data.zeroTradePenaltyActive).toBe(false);
  });

  it('zeroTradePenaltyActive is true when penalty is −100', () => {
    const data = buildFitnessBreakdownData({
      metricsJson,
      penaltyBreakdown: { ...BASELINE_PENALTY_BREAKDOWN, zero_trade_penalty: -100 },
    });
    expect(data.zeroTradePenaltyActive).toBe(true);
  });

  it('diversityPenaltyActive is false when factor is 1.0', () => {
    const data = buildFitnessBreakdownData({
      metricsJson,
      penaltyBreakdown: { ...BASELINE_PENALTY_BREAKDOWN, diversity_penalty_factor: 1.0 },
    });
    expect(data.diversityPenaltyActive).toBe(false);
  });

  it('diversityPenaltyActive is true when factor is 0.5', () => {
    const data = buildFitnessBreakdownData({
      metricsJson,
      penaltyBreakdown: { ...BASELINE_PENALTY_BREAKDOWN, diversity_penalty_factor: 0.5 },
    });
    expect(data.diversityPenaltyActive).toBe(true);
  });

  it('positive finalFitness signals the "green" outcome (fitness > 0)', () => {
    const data = buildFitnessBreakdownData({
      metricsJson,
      penaltyBreakdown: { ...BASELINE_PENALTY_BREAKDOWN, final_fitness: 0.35 },
    });
    expect(data.finalFitness).toBeGreaterThan(0);
  });

  it('negative finalFitness signals the "red" outcome (fitness ≤ 0)', () => {
    const data = buildFitnessBreakdownData({
      metricsJson,
      penaltyBreakdown: { ...BASELINE_PENALTY_BREAKDOWN, final_fitness: -0.12 },
    });
    expect(data.finalFitness).toBeLessThanOrEqual(0);
  });
});

describe('buildFitnessBreakdownData — legacy genome (gate_b_proximity_terms absent)', () => {
  const legacyMetricsJson = {
    sharpe: 0.65,
    max_drawdown: 0.08,
    hit_rate: 0.49,
    n_trades: 62,
    cagr: 0.18,
    // No raw_fitness, no gate_b_proximity_terms
  };

  it('returns null terms when gate_b_proximity_terms is absent', () => {
    const data = buildFitnessBreakdownData({
      metricsJson: legacyMetricsJson,
      penaltyBreakdown: BASELINE_PENALTY_BREAKDOWN,
    });
    expect(data.terms).toBeNull();
  });

  it('falls back to penaltyBreakdown.raw_fitness when metrics_json.raw_fitness is absent', () => {
    const data = buildFitnessBreakdownData({
      metricsJson: legacyMetricsJson,
      penaltyBreakdown: { ...BASELINE_PENALTY_BREAKDOWN, raw_fitness: 0.77 },
    });
    expect(data.rawFitness).toBe(0.77);
  });

  it('still reports penalties correctly on legacy rows', () => {
    const data = buildFitnessBreakdownData({
      metricsJson: legacyMetricsJson,
      penaltyBreakdown: {
        ...BASELINE_PENALTY_BREAKDOWN,
        zero_trade_penalty: -100,
        diversity_penalty_factor: 0.5,
        final_fitness: -100,
      },
    });
    expect(data.zeroTradePenaltyActive).toBe(true);
    expect(data.diversityPenaltyActive).toBe(true);
    expect(data.finalFitness).toBe(-100);
  });
});

describe('buildFitnessBreakdownData — gate_b_proximity_terms explicitly null', () => {
  it('returns null terms when the field is explicitly null', () => {
    const data = buildFitnessBreakdownData({
      metricsJson: { gate_b_proximity_terms: null },
      penaltyBreakdown: BASELINE_PENALTY_BREAKDOWN,
    });
    expect(data.terms).toBeNull();
  });
});
