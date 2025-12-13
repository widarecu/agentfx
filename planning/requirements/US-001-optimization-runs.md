# US-001: System Runs Optimization Experiments Autonomously

**Version:** v0.2.0 - Parameter Optimizer Agent
**Priority:** P0 - Must Have
**Status:** TODO

## Given-When-Then

**GIVEN** a strategy template (e.g., MA Crossover) and parameter ranges (e.g., fast: 5-50, slow: 20-200)
**WHEN** the optimizer agent is triggered via API
**THEN** the system:
1. Generates a grid of parameter combinations
2. Runs backtests for each combination
3. Stores each experiment run with full lineage (params, metrics, timestamp)
4. Identifies the top 3 parameter sets ranked by Sharpe ratio

## Acceptance Criteria

- [ ] `POST /api/v1/optimize` endpoint accepts strategy name and param ranges
- [ ] Optimization job runs asynchronously (returns job_id immediately)
- [ ] Each backtest run is persisted to `experiments` table
- [ ] Experiment records include: strategy_name, pair, params, metrics (Sharpe, MDD, hit_rate, trade_count)
- [ ] Job completion returns top 3 configurations sorted by Sharpe
- [ ] Lineage is preserved: experiment links to parent optimization job

## API Contract

### Request
```
POST /api/v1/optimize
Content-Type: application/json
Authorization: Bearer <token>

{
  "strategy": "ma",
  "pair": "EURUSD",
  "param_ranges": {
    "fast": {"min": 5, "max": 50, "step": 5},
    "slow": {"min": 20, "max": 200, "step": 10}
  },
  "data_days": 180,
  "validation_holdout_days": 30
}
```

### Response (Job Created)
```
HTTP 202 Accepted

{
  "job_id": "opt-abc123",
  "status": "queued",
  "strategy": "ma",
  "pair": "EURUSD",
  "total_combinations": 90,
  "created_at": "2025-01-15T10:00:00Z"
}
```

### Response (Job Complete - GET /api/v1/optimize/{job_id})
```
HTTP 200 OK

{
  "job_id": "opt-abc123",
  "status": "completed",
  "completed_at": "2025-01-15T10:15:00Z",
  "total_experiments": 90,
  "top_results": [
    {
      "rank": 1,
      "params": {"fast": 10, "slow": 50},
      "metrics": {
        "sharpe": 1.85,
        "max_drawdown": 0.06,
        "hit_rate": 0.54,
        "trade_count": 342
      },
      "experiment_id": "exp-xyz789"
    },
    ...
  ]
}
```

## Verification Commands

```bash
# Unit test - optimizer agent logic
PYTHONPATH=src pytest tests/unit/agents/test_optimizer.py -v

# Integration test - full optimization flow
PYTHONPATH=src pytest tests/integration/agents/test_optimizer_integration.py -v

# API contract test
PYTHONPATH=src pytest tests/contracts/test_optimize_api.py -v
```

## Database Schema

```sql
-- Optimization jobs table
CREATE TABLE optimization_jobs (
    id UUID PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    pair VARCHAR(10) NOT NULL,
    param_ranges JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'queued',
    total_combinations INT,
    completed_combinations INT DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    created_by UUID REFERENCES users(id)
);

-- Experiments table (individual backtest runs)
CREATE TABLE experiments (
    id UUID PRIMARY KEY,
    job_id UUID REFERENCES optimization_jobs(id),
    strategy_name VARCHAR(50) NOT NULL,
    pair VARCHAR(10) NOT NULL,
    params JSONB NOT NULL,
    metrics JSONB NOT NULL,
    data_start TIMESTAMP WITH TIME ZONE,
    data_end TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

## Files Involved

### Create
- `src/agentfx/agents/optimizer.py` - Optimizer agent implementation
- `src/agentfx/api/routers/optimize.py` - API endpoints
- `src/agentfx/api/models/experiment.py` - SQLAlchemy models
- `src/agentfx/api/schemas/optimize.py` - Pydantic schemas
- `tests/unit/agents/test_optimizer.py` - Unit tests
- `tests/integration/agents/test_optimizer_integration.py` - Integration tests
- `tests/contracts/test_optimize_api.py` - Contract tests

### Modify
- `src/agentfx/api/main.py` - Register optimize router
- `src/agentfx/services/backtest.py` - Add batch backtest support

## Dependencies

- Existing backtest engine (`src/agentfx/services/backtest.py`)
- Strategy implementations (`src/agentfx/core/strategy.py`)
- Database infrastructure (`src/agentfx/api/core/database.py`)

## Notes

- Start with simple grid search; Bayesian optimization is P1
- Use async job queue pattern (BackgroundTasks or Celery)
- Rate limit optimization requests per user
- Store intermediate results to allow job resume on failure
