/**
 * TypeScript mirror of `src/agentfx/api/schemas/paper.py` (US-021 / US-023).
 *
 * Field names are the API's snake_case verbatim so JSON deserializes
 * cleanly. UI-only concerns (Gate B evaluation, status pill styling,
 * quick-pick reject reasons) live alongside the raw types.
 */

// ─────────────────────────────────────────────────────────────────────
// Status vocabulary — matches Postgres `runstatus` enum labels
// (all UPPERCASE per the PR-F migration comment).
// ─────────────────────────────────────────────────────────────────────

export type PaperRunStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'PAUSED'
  | 'COMPLETED'
  | 'ERROR'
  | 'AWAITING_APPROVAL'
  | 'APPROVED'
  | 'HALTED_MANUAL';

export const PAPER_RUN_TERMINAL: readonly PaperRunStatus[] = [
  'COMPLETED',
  'ERROR',
  'HALTED_MANUAL',
] as const;

export function isTerminal(status: PaperRunStatus): boolean {
  return (PAPER_RUN_TERMINAL as readonly string[]).includes(status);
}

// ─────────────────────────────────────────────────────────────────────
// API shape
// ─────────────────────────────────────────────────────────────────────

export interface PaperRunListItem {
  id: string;
  status: PaperRunStatus;
  instrument: string;
  granularity: string;
  oanda_account_id: string | null;
  pnl: number | null;
  snapshot_balance: number | null;
  stop_reason: string | null;
  created_at: string | null;
}

export interface PaperRunList {
  items: PaperRunListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface RiskConfig {
  max_position_size_units: number;
  daily_loss_pct: number;
  kill_switch_dd_pct: number;
}

export interface PaperRunPublic {
  id: string;
  status: PaperRunStatus;
  strategy_genome_id: string | null;
  /** Parent evolution run — resolved server-side so the frontend can
   *  link back to `/evolution/{evolution_run_id}/genomes/{strategy_genome_id}`.
   *  Null when the genome or its parents have been deleted. */
  evolution_run_id: string | null;
  instrument: string;
  granularity: string;
  oanda_account_id: string | null;
  oanda_environment: string | null;
  snapshot_balance: number | null;
  current_balance: number | null;
  pnl: number | null;
  risk_config: RiskConfig | null;
  stop_reason: string | null;
  kill_requested_at: string | null;
  approved_at: string | null;
  approved_by_user_id: number | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  user_id: number | null;
}

export interface ApprovalQueueItem {
  strategy_genome_id: string;
  run_id: string;
  instrument: string;
  granularity: string;
  fitness: number | null;
  /** Full metrics blob from the genome — Gate B eval reads this. */
  metrics_summary: Record<string, unknown> | null;
  created_at: string | null;
}

export interface ApprovalQueueList {
  items: ApprovalQueueItem[];
  total: number;
}

export interface KillPaperRunResponse {
  run_id: string;
  kill_requested_at: string;
  message: string;
}

// ─────────────────────────────────────────────────────────────────────
// Gate B evaluation — UI-only helper used by ApprovalQueueCard to
// colour each metric independently. Thresholds mirror the backend
// (see `planning/MVP_CONSTRAINTS.md` + `src/agentfx/core/gates.py`).
// Kept as data + a pure function so the Vitest suite locks the
// boundary conditions in one place.
// ─────────────────────────────────────────────────────────────────────

export const GATE_B_THRESHOLDS = {
  sharpe_min: 1.0,
  max_drawdown_max: 0.10, // 10% (as a fraction, negative == drawdown)
  hit_rate_min: 0.48,
  trades_min: 300,
} as const;

export type MetricVerdict = 'pass' | 'borderline' | 'fail';

/**
 * Evaluate a single metric against Gate B.
 *
 * `borderline` is a UI-only bucket for metrics that pass but sit
 * within 10% of the threshold — matches the wireframe's amber colour
 * intent (Wilson: "cards for genomes that fully clear Gate B get a
 * subtle green border to distinguish them").
 */
export function evaluateSharpe(sharpe: number | null | undefined): MetricVerdict {
  if (sharpe == null) return 'fail';
  if (sharpe < GATE_B_THRESHOLDS.sharpe_min) return 'fail';
  if (sharpe < GATE_B_THRESHOLDS.sharpe_min * 1.1) return 'borderline';
  return 'pass';
}

export function evaluateMaxDrawdown(
  maxDrawdown: number | null | undefined,
): MetricVerdict {
  if (maxDrawdown == null) return 'fail';
  // Values arrive as negative or absolute; normalise to absolute.
  const abs = Math.abs(maxDrawdown);
  if (abs > GATE_B_THRESHOLDS.max_drawdown_max) return 'fail';
  if (abs > GATE_B_THRESHOLDS.max_drawdown_max * 0.9) return 'borderline';
  return 'pass';
}

export function evaluateHitRate(
  hitRate: number | null | undefined,
): MetricVerdict {
  if (hitRate == null) return 'fail';
  if (hitRate < GATE_B_THRESHOLDS.hit_rate_min) return 'fail';
  if (hitRate < GATE_B_THRESHOLDS.hit_rate_min * 1.05) return 'borderline';
  return 'pass';
}

export function evaluateTrades(
  trades: number | null | undefined,
): MetricVerdict {
  if (trades == null) return 'fail';
  if (trades < GATE_B_THRESHOLDS.trades_min) return 'fail';
  if (trades < GATE_B_THRESHOLDS.trades_min * 1.05) return 'borderline';
  return 'pass';
}

/** Aggregate: does the genome clear every Gate B threshold? */
export function clearsGateB(
  metrics: Record<string, unknown> | null | undefined,
): boolean {
  if (!metrics) return false;
  const s = evaluateSharpe(toNumber(metrics.sharpe));
  const dd = evaluateMaxDrawdown(toNumber(metrics.max_drawdown));
  const hr = evaluateHitRate(toNumber(metrics.hit_rate));
  const t = evaluateTrades(toNumber(metrics.trades) ?? toNumber(metrics.n_trades));
  return [s, dd, hr, t].every((v) => v !== 'fail');
}

function toNumber(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string') {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

// ─────────────────────────────────────────────────────────────────────
// Reject reasons — advisory quick-pick chips. The API stores the
// free-text field; the enum values below are just UI shortcuts.
// Any custom reason works too.
// ─────────────────────────────────────────────────────────────────────

export const REJECT_QUICK_PICKS: readonly string[] = [
  'Trade count too low',
  'Sharpe borderline',
  'Overfit to regime',
  'Similar to running genome',
  'Wait for re-evolution',
] as const;

// ─────────────────────────────────────────────────────────────────────
// Concurrency banner threshold. Wired here so the wireframe copy
// (10+ runs → amber banner) stays consistent across list + admin view.
// ─────────────────────────────────────────────────────────────────────

export const CONCURRENCY_WARN_THRESHOLD = 10;

/**
 * Status pill palette — used by PaperRunListItem row + ApprovalQueue
 * badges. Kept as a lookup so unknown / future statuses fall back to
 * neutral slate without a runtime error.
 */
export const STATUS_PILL_CLASSES: Record<PaperRunStatus, string> = {
  PENDING: 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  RUNNING:
    'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  PAUSED:
    'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  COMPLETED: 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  ERROR: 'bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300',
  AWAITING_APPROVAL:
    'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300',
  APPROVED:
    'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  HALTED_MANUAL:
    'bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300',
};
