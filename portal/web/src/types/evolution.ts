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
  /**
   * Opt-in: when true, the loop early-stops on training fitness ≥
   * target_fitness. Default false because training-fitness early-stop
   * routinely sacrifices a more-robust later-generation candidate.
   */
  enable_target_fitness_stop?: boolean;
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
  /** OANDA retail leverage on majors: 30:1 default, 50:1 opt-in. */
  leverage: number;
  /** Gen-0 seeding controls — see backend OrchestratorConfig. */
  use_prior_winners?: boolean;
  n_prior_winners?: number;
  llm_seed_gen0?: boolean;
  n_llm_gen0_proposals?: number;
  /** Gate B override: min net CAGR per year (default 0.15 = 15%). */
  min_cagr_pct?: number;
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
  config_json: Record<string, unknown> & {
    data_years?: number;
    llm_budget_usd?: number;
    /** User-requested explicit window, as ISO strings. */
    data_window_start?: string;
    data_window_end?: string;
    /** Effective values pinned by the worker at run-start. Always set
     * once the run has actually started; used to display "what was
     * actually used" and to make the run reproducible weeks later. */
    effective_data_window_start?: string;
    effective_data_window_end?: string;
    effective_spread_pips?: number;
    /** Set by the worker only when the run failed with a structured
     * reason (e.g. data_window_not_covered). Free-form human message
     * meant for the portal to surface verbatim. */
    failure_message?: string;
  };
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
  /** Pin the data window explicitly (ISO strings). When both are set,
   * the worker uses them verbatim and ignores `data_years`. Makes the
   * run reproducible regardless of when it's launched. */
  data_window_start?: string;
  data_window_end?: string;
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

export type GenomeOrigin =
  | 'seed'
  | 'warm_start'
  | 'crossover'
  | 'mutation'
  | 'llm';

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
  cagr: number | null;
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
export interface GenomeBacktestSizing {
  account_balance: number;
  account_currency: string;
  leverage: number;
  leverage_cap_hits: number;
  is_dsl_risk_sized: boolean;
  /**
   * Compounded sizing block (added when capture_signals=True). The
   * `starting_balance` is the initial capital; `ending_balance` is what
   * the running balance worked down to after every closed trade's net
   * P&L was applied; `peak_balance` is the high-water mark along the
   * way. `compounded=true` means subsequent trades were sized against
   * the running balance, not the static starting capital.
   */
  starting_balance?: number;
  ending_balance?: number;
  peak_balance?: number;
  compounded?: boolean;
}

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
  sizing?: GenomeBacktestSizing;
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
