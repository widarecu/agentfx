/**
 * TypeScript types matching the backend's evolution Pydantic schemas
 * (`src/agentfx/api/schemas/evolution.py`).
 *
 * Field names match the API exactly (snake_case) so JSON responses
 * deserialize without translation.
 */

// ─────────────────────────────────────────────────────────────────────
// Configuration sub-types
// ─────────────────────────────────────────────────────────────────────

export interface StopConditions {
  max_generations: number;
  max_wall_clock_seconds: number;
  target_fitness: number;
  no_improvement_generations: number;
}

export interface OrchestratorConfig {
  instrument: string;
  granularity: string;
  population_size: number;
  llm_injection_every_n: number;
  n_llm_proposals: number;
  n_validation_folds: number;
  top_n_to_validate: number;
  crossover_prob: number;
  mutation_prob: number;
  elite_fraction: number;
  tournament_size: number;
  rng_seed: number;
  account_balance: number;
  position_size: number;
  spread_pips: number | null;
}

// ─────────────────────────────────────────────────────────────────────
// Evolution run
// ─────────────────────────────────────────────────────────────────────

export type EvolutionRunStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'stopped';

export interface EvolutionRun {
  id: string;
  status: EvolutionRunStatus;
  instrument: string;
  granularity: string;
  config_json: Record<string, unknown> & { data_years?: number; llm_budget_usd?: number };
  stop_conditions_json: Record<string, unknown>;
  stop_reason: string | null;
  current_generation: number;
  best_genome_id: string | null;
  llm_cost_usd: string;  // Decimal serializes as string
  process_pid: number | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface EvolutionRunListResponse {
  items: EvolutionRun[];
  total: number;
  page: number;
  page_size: number;
}

export interface EvolutionRunCreateRequest {
  instrument: string;
  granularity?: string;
  data_years?: number;
  config?: Partial<OrchestratorConfig>;
  stop_conditions?: Partial<StopConditions>;
  llm_budget_usd?: number;
}

export interface EvolutionRunCreated {
  run_id: string;
  status: string;
  status_url: string;
  instrument: string;
  granularity: string;
  estimated_max_generations: number;
  created_at: string;
}

// ─────────────────────────────────────────────────────────────────────
// Generation
// ─────────────────────────────────────────────────────────────────────

export interface Generation {
  id: string;
  run_id: string;
  index: number;
  started_at: string | null;
  completed_at: string | null;
  llm_invoked: boolean;
  llm_call_id: string | null;
  best_fitness: number | null;
}

export interface GenerationListResponse {
  items: Generation[];
  total: number;
}

// ─────────────────────────────────────────────────────────────────────
// Strategy genome
// ─────────────────────────────────────────────────────────────────────

export type GenomeOrigin = 'seed' | 'crossover' | 'mutation' | 'llm';

export interface ValidationFold {
  id: string;
  fold_index: number;
  fold_start: string;
  fold_end: string;
  is_oos: boolean;
  sharpe: number | null;
  max_drawdown: number | null;
  hit_rate: number | null;
  n_trades: number | null;
  passed_gate_b: boolean;
}

/**
 * The DSL spec — kept loosely-typed because the structure is recursive
 * (AND/OR conditions can nest). The DslTree component handles the
 * walking; everywhere else just treats this as opaque JSON.
 */
export type DslSpec = {
  version?: number;
  entry?: {
    long?: DslCondition | null;
    short?: DslCondition | null;
  };
  exit?: {
    stop_pips?: number;
    target_pips?: number;
    max_hold_bars?: number;
    opposite_signal?: boolean;
  };
  size?: { risk_pct?: number };
  [k: string]: unknown;
};

export type DslExpr =
  | number
  | { ind: string; args: number[] };

export type DslComparison = {
  lhs: DslExpr;
  cmp: '>' | '<' | '>=' | '<=' | 'crosses_above' | 'crosses_below';
  rhs: DslExpr;
};

export type DslLogical = {
  op: 'AND' | 'OR';
  args: DslCondition[];
};

export type DslCondition = DslComparison | DslLogical;

export interface GenomeChild {
  id: string;
  generation_index: number;
}

/**
 * Lightweight projection of a genome — used for the run-wide lineage tree.
 * Excludes the heavy `dsl_json` blob and validation folds; click through to
 * the full `StrategyGenome` via `getGenome(runId, id)` for drill-in.
 */
export interface StrategyGenomeSummary {
  id: string;
  generation_index: number;
  parent_ids: string[];
  origin: GenomeOrigin;
  fitness: number | null;
  validator_passed: boolean | null;
}

export interface StrategyGenomeSummaryList {
  items: StrategyGenomeSummary[];
  total: number;
}

/**
 * Result of the on-demand backtest endpoint
 * (`GET /evolution-runs/{run_id}/genomes/{id}/backtest`).
 *
 * Shapes for `trades` and `signals` are intentionally loose (Record) so
 * the existing Trade / TradeSignal interfaces from `types/optimization.ts`
 * can be used directly — the snake_case → camelCase mapping happens in
 * the API-client layer, mirroring the trial-detail flow.
 */
export interface GenomeBacktestResult {
  run_id: string;
  genome_id: string;
  instrument: string;
  granularity: string;
  data_range_start: string;
  data_range_end: string;
  metrics: Record<string, number>;
  trades: Record<string, unknown>[];
  signals: Record<string, unknown>[];
}

export interface StrategyGenome {
  id: string;
  generation_id: string;
  generation_index: number;
  dsl_json: DslSpec;
  parent_ids: string[];
  origin: GenomeOrigin;
  fitness: number | null;
  validator_passed: boolean | null;
  validation_failures: string[];
  llm_reasoning: string | null;
  llm_call_id: string | null;
  created_at: string;
  children: GenomeChild[];
  validation_folds: ValidationFold[];
}

// ─────────────────────────────────────────────────────────────────────
// Decision log
// ─────────────────────────────────────────────────────────────────────

export type DecisionAgentType = 'orchestrator' | 'ga' | 'llm' | 'validator';

export interface DecisionLogEntry {
  id: string;
  run_id: string;
  generation_id: string | null;
  agent_type: string;  // open-ended; UI should treat as freeform
  action: string;
  inputs_json: Record<string, unknown> | null;
  outputs_json: Record<string, unknown> | null;
  reasoning_text: string | null;
  llm_call_id: string | null;
  created_at: string;
}

export interface DecisionLogListResponse {
  items: DecisionLogEntry[];
  total: number;
  page: number;
  page_size: number;
}
