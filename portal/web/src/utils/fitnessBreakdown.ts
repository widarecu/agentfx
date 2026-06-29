/**
 * Pure computation helpers for the Gate-B-proximity fitness breakdown panel.
 *
 * Extracted from FitnessBreakdown.tsx so the business logic is testable in
 * the Vitest node environment without a DOM or React renderer.
 *
 * Gate B thresholds (v0.6.0 defaults):
 *   CAGR    ≥ 15%   weight 0.35
 *   MDD     ≤ 10%   weight 0.25
 *   Sharpe  ≥  0.5  weight 0.20
 *   Hit     ≥ 45%   weight 0.10
 *   Trades  ≥ 30    weight 0.10
 */

export interface GateBTermWeights {
  cagr: number;
  mdd: number;
  sharpe: number;
  hit: number;
  trades: number;
}

/** Default weights as defined in the v0.6.0 backend fitness.py */
export const DEFAULT_WEIGHTS: GateBTermWeights = {
  cagr: 0.35,
  mdd: 0.25,
  sharpe: 0.20,
  hit: 0.10,
  trades: 0.10,
};

export interface ProximityTerm {
  /** Human-readable metric label */
  label: string;
  /** Gate B threshold shown as a string (e.g. "≥ 15%") */
  threshold: string;
  /** The raw metric value as a number (null when unavailable) */
  metricValue: number | null;
  /** Formatted metric value for display (e.g. "12.3%") */
  metricFormatted: string;
  /** Normalised term score (0 = at threshold, positive = exceeding) */
  termValue: number;
  /** Fractional weight applied to this term (e.g. 0.35) */
  weight: number;
  /** weight × termValue — the contribution to the raw fitness composite */
  contribution: number;
  /** Whether the metric passes its Gate B threshold */
  passing: boolean;
}

export interface FitnessBreakdownData {
  /** Five weighted proximity terms (present when gate_b_proximity_terms exists) */
  terms: ProximityTerm[] | null;
  /**
   * Raw Gate-B-proximity composite.
   * v0.6.0+: from metrics_json.raw_fitness (dedicated field).
   * Fallback for legacy rows: from penalty_breakdown.raw_fitness.
   */
  rawFitness: number;
  /** Whether the zero-trade hard floor (−100) was applied */
  zeroTradePenaltyActive: boolean;
  /** Whether the diversity penalty (×0.5) was applied */
  diversityPenaltyActive: boolean;
  /** Final fitness after all penalties */
  finalFitness: number;
}

/** Format a signed number with a leading '+' for positives */
export function fmtSigned(v: number, digits = 3): string {
  if (!Number.isFinite(v)) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(digits);
}

/** Format a percentage value (0.123 → "12.3%") */
export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

/** Format a plain decimal number */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

/**
 * Build the five ProximityTerm rows from the metrics snapshot and the
 * per-term normalised scores the backend emits.
 *
 * @param proximityTerms  The `gate_b_proximity_terms` object from metrics_json
 * @param metrics         Aggregated metric values from metrics_json (cagr, mdd, …)
 * @param weights         Per-term weights (defaults to DEFAULT_WEIGHTS)
 */
export function buildProximityTerms(
  proximityTerms: {
    sharpe_term: number;
    cagr_term: number;
    hit_term: number;
    mdd_term: number;
    trades_term: number;
  },
  metrics: {
    cagr?: number | null;
    max_drawdown?: number | null;
    sharpe?: number | null;
    hit_rate?: number | null;
    n_trades?: number | null;
  },
  weights: GateBTermWeights = DEFAULT_WEIGHTS,
): ProximityTerm[] {
  const cagr: ProximityTerm = {
    label: 'CAGR',
    threshold: '≥ 15%',
    metricValue: metrics.cagr ?? null,
    metricFormatted: fmtPct(metrics.cagr),
    termValue: proximityTerms.cagr_term,
    weight: weights.cagr,
    contribution: weights.cagr * proximityTerms.cagr_term,
    passing: proximityTerms.cagr_term >= 0,
  };

  const mdd: ProximityTerm = {
    label: 'Max Drawdown',
    threshold: '≤ 10%',
    metricValue: metrics.max_drawdown ?? null,
    metricFormatted: fmtPct(metrics.max_drawdown),
    termValue: proximityTerms.mdd_term,
    weight: weights.mdd,
    contribution: weights.mdd * proximityTerms.mdd_term,
    // MDD term: positive when drawdown is BETTER (lower) than the threshold.
    passing: proximityTerms.mdd_term >= 0,
  };

  const sharpe: ProximityTerm = {
    label: 'Sharpe',
    threshold: '≥ 0.5',
    metricValue: metrics.sharpe ?? null,
    metricFormatted: fmtNum(metrics.sharpe),
    termValue: proximityTerms.sharpe_term,
    weight: weights.sharpe,
    contribution: weights.sharpe * proximityTerms.sharpe_term,
    passing: proximityTerms.sharpe_term >= 0,
  };

  const hit: ProximityTerm = {
    label: 'Hit Rate',
    threshold: '≥ 45%',
    metricValue: metrics.hit_rate ?? null,
    metricFormatted: fmtPct(metrics.hit_rate),
    termValue: proximityTerms.hit_term,
    weight: weights.hit,
    contribution: weights.hit * proximityTerms.hit_term,
    passing: proximityTerms.hit_term >= 0,
  };

  const trades: ProximityTerm = {
    label: 'Trades',
    threshold: '≥ 30',
    metricValue: metrics.n_trades ?? null,
    metricFormatted:
      metrics.n_trades != null && Number.isFinite(metrics.n_trades)
        ? String(Math.round(metrics.n_trades))
        : '—',
    termValue: proximityTerms.trades_term,
    weight: weights.trades,
    contribution: weights.trades * proximityTerms.trades_term,
    passing: proximityTerms.trades_term >= 0,
  };

  return [cagr, mdd, sharpe, hit, trades];
}

/**
 * Derive the full FitnessBreakdownData from a genome's metrics_json and
 * penalty_breakdown. Handles both the new v0.6.0 shape (with
 * gate_b_proximity_terms) and legacy shapes (without).
 */
export function buildFitnessBreakdownData(params: {
  metricsJson: {
    raw_fitness?: number | null;
    gate_b_proximity_terms?: {
      sharpe_term: number;
      cagr_term: number;
      hit_term: number;
      mdd_term: number;
      trades_term: number;
    } | null;
    cagr?: number | null;
    max_drawdown?: number | null;
    sharpe?: number | null;
    hit_rate?: number | null;
    n_trades?: number | null;
    [k: string]: unknown;
  };
  penaltyBreakdown: {
    raw_fitness: number;
    zero_trade_penalty: number;
    diversity_penalty_factor: number;
    final_fitness: number;
    [k: string]: unknown;
  };
  weights?: GateBTermWeights;
}): FitnessBreakdownData {
  const { metricsJson, penaltyBreakdown, weights = DEFAULT_WEIGHTS } = params;

  const terms =
    metricsJson.gate_b_proximity_terms != null
      ? buildProximityTerms(metricsJson.gate_b_proximity_terms, metricsJson, weights)
      : null;

  // Prefer the dedicated raw_fitness field (v0.6.0+); fall back to the
  // penalty_breakdown.raw_fitness which is the same value for new rows
  // and the old Sharpe-based value for legacy rows.
  const rawFitness =
    metricsJson.raw_fitness != null
      ? metricsJson.raw_fitness
      : penaltyBreakdown.raw_fitness;

  return {
    terms,
    rawFitness,
    zeroTradePenaltyActive: penaltyBreakdown.zero_trade_penalty !== 0,
    diversityPenaltyActive: penaltyBreakdown.diversity_penalty_factor !== 1.0,
    finalFitness: penaltyBreakdown.final_fitness,
  };
}
