# US-016: Evolution Run Observability — Decision Log + Portal

**Version:** v0.3.0 - Autonomous Strategy Loop
**Priority:** P0 - Must Have
**Status:** TODO

## Given-When-Then

**GIVEN** an evolution run is in progress (or completed) and a TRADER opens its detail page in the portal
**WHEN** they click into a specific genome (especially one that the system promoted to VALIDATED)
**THEN** they can answer the question *"why did the system pick this genome?"* without reading any code by inspecting:
1. The DSL spec rendered as a readable tree (entry conditions, exit rules, sizing)
2. The full lineage — parent genomes (1 for mutation, 2 for crossover, 0 for LLM/seed) and child genomes derived from it
3. The walk-forward fold metrics (Sharpe / MDD / hit rate / n_trades for each of the 4 folds, with the OOS fold visually distinct)
4. For LLM-origin genomes: the LLM's reasoning text and a link to the originating `llm_call_id`
5. The decision log — chronological list of every agent action (population init, genome evaluation, LLM call, mutation, crossover, selection, validation) — with reasoning text where applicable

## Acceptance Criteria

- [ ] `decision_log` table populated by every agent step. Append-only. No updates, no deletes.
- [ ] `EvolutionRunDetail.tsx` page renders:
  - Header: run status, instrument, current/best Sharpe, generation count, LLM cost, stop reason (if completed)
  - Fitness curve (matplotlib-style line chart of best fitness per generation; library: existing `lightweight-charts` if feasible, else `recharts`)
  - Decision log viewer: paginated table, filterable by `agent_type` and `action`
  - List of validated genomes with quick metrics + Pass badge
- [ ] `GenomeDetail.tsx` page renders:
  - DSL tree (custom React component recursively rendering entry conditions / indicators / comparisons) — must be readable, not raw JSON
  - Walk-forward fold table (4 rows, one per fold, OOS row highlighted)
  - Lineage section: clickable parent links + child links
  - LLM reasoning text (only shown if `genome.origin = 'llm'`)
  - Reuses [portal/web/src/components/StrategyChart.tsx](../../portal/web/src/components/StrategyChart.tsx) for candle + signal rendering on the OOS fold's signals
- [ ] `EvolutionRuns.tsx` list page mirrors the [Optimization.tsx](../../portal/web/src/pages/Optimization.tsx) format: paginated table with filters (status, instrument), "Start Run" button (TRADER+ only)
- [ ] Routes wired in [portal/web/src/router.tsx](../../portal/web/src/router.tsx): `/evolution`, `/evolution/:runId`, `/evolution/:runId/genomes/:genomeId`
- [ ] LLM cost emitted as Prometheus metric `evolution_llm_cost_usd_total{run_id, model}` via existing [api/core/metrics.py](../../src/agentfx/api/core/metrics.py)
- [ ] LLM call latency emitted as `evolution_llm_call_duration_seconds` histogram

## API Contract (read-only endpoints supporting the portal)

### Get Decision Log
```
GET /api/v1/evolution-runs/{run_id}/decisions?agent_type=optimizer&page=1&page_size=50
```

```json
{
  "items": [
    {
      "id": "uuid",
      "run_id": "evorun_…",
      "generation_id": "uuid|null",
      "agent_type": "ga|llm|validator|orchestrator",
      "action": "mutate|crossover|select|inject|evaluate|validate|stop",
      "inputs_json": {...},
      "outputs_json": {...},
      "reasoning_text": "string|null",
      "llm_call_id": "string|null",
      "created_at": "..."
    }
  ],
  "total": 1234,
  "page": 1,
  "page_size": 50
}
```

### Get Genome Detail (with lineage)
```
GET /api/v1/evolution-runs/{run_id}/genomes/{genome_id}
```

```json
{
  "id": "uuid",
  "run_id": "evorun_…",
  "generation_id": "uuid",
  "generation_index": 12,
  "dsl_json": {...},
  "origin": "llm|crossover|mutation|seed",
  "parent_ids": ["uuid", ...],
  "children": [{"id": "uuid", "generation_index": 13}, ...],
  "fitness": 1.42,
  "validator_passed": true,
  "validation_failures": [],
  "validation_folds": [
    {"fold_index": 0, "is_oos": false, "sharpe": 1.1, ...},
    ...,
    {"fold_index": 3, "is_oos": true,  "sharpe": 1.42, "passed_gate_b": true}
  ],
  "llm_reasoning": "string|null",
  "llm_call_id": "string|null"
}
```

### Get Generations (lineage)
```
GET /api/v1/evolution-runs/{run_id}/generations
```

## Database Schema (additions)

```sql
CREATE TABLE decision_log (
    id              UUID PRIMARY KEY,
    run_id          VARCHAR(50) REFERENCES evolution_run(id) ON DELETE CASCADE,
    generation_id   UUID REFERENCES generation(id) ON DELETE SET NULL,
    agent_type      VARCHAR(30) NOT NULL,    -- ga|llm|validator|orchestrator
    action          VARCHAR(40) NOT NULL,    -- mutate|crossover|select|inject|evaluate|validate|stop|...
    inputs_json     JSONB,
    outputs_json    JSONB,
    reasoning_text  TEXT,
    llm_call_id     VARCHAR(80),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_decision_log_run_created ON decision_log(run_id, created_at DESC);
CREATE INDEX idx_decision_log_agent_action ON decision_log(agent_type, action);
```

The decision log is the audit trail Gate-B promotion (and eventually live trading) will depend on. It is NEVER mutated.

## Files Involved

### Create
- `src/agentfx/agents/evolution/decisions.py` — `log_decision(run_id, agent, action, inputs, outputs, reasoning)` helper used by every agent
- `portal/web/src/pages/EvolutionRuns.tsx`
- `portal/web/src/pages/EvolutionRunDetail.tsx`
- `portal/web/src/pages/GenomeDetail.tsx`
- `portal/web/src/components/DslTree.tsx` — recursive renderer for `StrategySpec`
- `portal/web/src/components/DecisionLogTable.tsx`
- `portal/web/src/components/FitnessCurveChart.tsx`
- `portal/web/src/components/WalkForwardFoldTable.tsx`
- `portal/web/src/components/GenomeLineage.tsx`
- `portal/web/src/types/evolution.ts` — TypeScript types matching backend Pydantic schemas
- `portal/web/src/services/evolutionApi.ts` — API client functions
- `tests/e2e/evolution_run.spec.ts`
- `tests/e2e/genome_detail.spec.ts`

### Modify
- `portal/web/src/router.tsx` — add three new routes (list, detail, genome detail) under `ProtectedRoute`
- `portal/web/src/App.tsx` — add nav link to `/evolution`
- `src/agentfx/api/core/metrics.py` — register `evolution_llm_cost_usd_total` Counter and `evolution_llm_call_duration_seconds` Histogram

## Verification Commands

```bash
# E2E
make e2e   # runs all specs including the new evolution ones
```

## Dependencies

- US-003 (Evolution Run Lifecycle) — provides the data
- US-004 (Walk-forward Validation) — provides the fold metrics
- US-005 (LLM Hypothesis Injection) — provides the reasoning text
- US-006 (Strategy DSL) — `DslTree.tsx` renders against the v1 grammar

## Notes

- The decision log is the *most* important artefact of v0.3.0 from a product standpoint. Without it, the system is a black box that picks strategies for reasons no human can audit. With it, the system is a research assistant whose work can be reviewed before being trusted with real money.
- The DSL tree component must be hand-written, not generic JSON-tree, because the user is reading it as *a strategy*, not as *data*. Indicator function calls should look like `EMA(close, 20)`, not `{ind: "ema", args: [20]}`.
- For LLM-origin genomes, the reasoning text is what the LLM itself said *when proposing* the genome. It is not retroactively generated. This matters: the LLM's stated rationale is auditable evidence of *why a hypothesis was tried*, even if the hypothesis later succeeded for unrelated reasons.
- The fitness curve is the single most useful visual for spotting "the run plateaued and then the LLM injection bumped it" — the moment that justifies the LLM's existence in the loop.
