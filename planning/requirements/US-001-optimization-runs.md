# US-001: System Runs Bayesian Optimization Autonomously

**Version:** v0.2.0 - Bayesian Parameter Optimizer Agent
**Priority:** P0 - Must Have
**Status:** TODO

## Given-When-Then

**GIVEN** historical data exists for the specified pair (e.g., EUR_USD) in PostgreSQL candles table
  **AND** the user is authenticated as TRADER or ADMIN role
**WHEN** `POST /api/v1/optimization-jobs` is called with strategy template and parameter ranges
**THEN** the system:
1. Returns 202 Accepted with job_id immediately
2. Runs Bayesian optimization in background using Expected Improvement acquisition
3. Agent auto-determines optimal iteration count (~40-60 based on parameter dimensions)
4. Stores all trials with full lineage (params, metrics, signals)
5. Captures trade signals for each trial (for visualization)
6. Identifies top 3 configurations by Sharpe ratio
7. Evaluates Gate B thresholds (informational only, not enforced)

## Acceptance Criteria

- [ ] `POST /api/v1/optimization-jobs` endpoint accepts strategy name and param ranges
- [ ] Returns 202 Accepted with job_id (async pattern per STANDARDS.md)
- [ ] Bayesian optimization converges in fewer iterations than equivalent grid search
- [ ] Each trial is persisted to `optimization_trials` table with full lineage
- [ ] Trial records include: params, metrics (Sharpe, MDD, hit_rate, trade_count), signals
- [ ] Job completion returns top 3 configurations sorted by Sharpe
- [ ] Gate B thresholds evaluated: Sharpe >= 1.0, MDD <= 10%, Hit Rate >= 48%
- [ ] Backtest service reads from PostgreSQL candles table (not CSV files)
- [ ] Authorization: TRADER or ADMIN role required to create jobs

## API Contract

### Create Optimization Job
```
POST /api/v1/optimization-jobs
Content-Type: application/json
Authorization: Bearer <JWT_TOKEN>

{
  "strategy": "ma",
  "pair": "EUR_USD",
  "param_ranges": {
    "fast": {"min": 5, "max": 50},
    "slow": {"min": 20, "max": 200}
  },
  "data_years": 5,
  "validation_holdout_days": 30
}
```

### Response (Job Created)
```
HTTP 202 Accepted

{
  "job_id": "optjob_20250115_abc123",
  "status": "queued",
  "status_url": "/api/v1/optimization-jobs/optjob_20250115_abc123",
  "strategy": "ma",
  "pair": "EUR_USD",
  "estimated_iterations": 55,
  "created_at": "2025-01-15T10:00:00Z"
}
```

### Get Job Status
```
GET /api/v1/optimization-jobs/{job_id}
Authorization: Bearer <JWT_TOKEN>
```

### Response (Job Complete)
```
HTTP 200 OK

{
  "job_id": "optjob_20250115_abc123",
  "status": "completed",
  "strategy": "ma",
  "pair": "EUR_USD",
  "completed_at": "2025-01-15T10:15:00Z",
  "total_trials": 55,
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
      "passes_gate_b": true,
      "gate_b_failures": [],
      "trial_id": "trial_optjob_20250115_abc123_001"
    },
    {
      "rank": 2,
      "params": {"fast": 15, "slow": 60},
      "metrics": {
        "sharpe": 1.72,
        "max_drawdown": 0.07,
        "hit_rate": 0.52,
        "trade_count": 298
      },
      "passes_gate_b": true,
      "gate_b_failures": [],
      "trial_id": "trial_optjob_20250115_abc123_002"
    }
  ]
}
```

### List All Trials
```
GET /api/v1/optimization-jobs/{job_id}/trials
Authorization: Bearer <JWT_TOKEN>
```

### Get Trial Signals (for visualization)
```
GET /api/v1/optimization-jobs/{job_id}/trials/{trial_id}/signals
Authorization: Bearer <JWT_TOKEN>
```

### Response (Trial Signals)
```
HTTP 200 OK

{
  "trial_id": "trial_optjob_20250115_abc123_001",
  "signals": [
    {
      "timestamp": "2020-01-15T10:00:00Z",
      "signal": 1,
      "signal_type": "entry_long",
      "price": 1.1234,
      "indicator_values": {"fast_ma": 1.1220, "slow_ma": 1.1200}
    },
    {
      "timestamp": "2020-01-20T14:30:00Z",
      "signal": 0,
      "signal_type": "exit_long",
      "price": 1.1345,
      "indicator_values": {"fast_ma": 1.1330, "slow_ma": 1.1340}
    }
  ]
}
```

## Authorization Matrix

| Action | Minimum Role |
|--------|-------------|
| Create optimization job | TRADER |
| View jobs/trials | VIEWER |
| Delete own job | TRADER |
| Delete any job | ADMIN |

## Verification Commands

```bash
# Unit test - Bayesian optimizer agent logic
PYTHONPATH=src pytest tests/unit/agents/test_bayesian_optimizer.py -v

# Integration test - full optimization flow
PYTHONPATH=src pytest tests/integration/agents/test_optimizer_integration.py -v

# API contract test
PYTHONPATH=src pytest tests/contracts/test_optimization_api.py -v
```

## Database Schema

```sql
-- Optimization jobs table
CREATE TABLE optimization_jobs (
    id VARCHAR(50) PRIMARY KEY,  -- Format: optjob_{timestamp}_{short_uuid}
    strategy_name VARCHAR(50) NOT NULL,
    pair VARCHAR(10) NOT NULL,
    param_ranges JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'queued',  -- queued, running, completed, failed
    estimated_iterations INT,
    completed_iterations INT DEFAULT 0,
    data_start TIMESTAMP WITH TIME ZONE,
    data_end TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    created_by UUID REFERENCES users(id)
);

-- Optimization trials table (individual backtest runs)
CREATE TABLE optimization_trials (
    id VARCHAR(80) PRIMARY KEY,  -- Format: trial_{job_id}_{index}
    job_id VARCHAR(50) REFERENCES optimization_jobs(id),
    params JSONB NOT NULL,
    metrics JSONB NOT NULL,  -- sharpe, max_drawdown, hit_rate, trade_count, etc.
    passes_gate_b BOOLEAN DEFAULT FALSE,
    gate_b_failures JSONB DEFAULT '[]',
    signals JSONB,  -- Trade signals for visualization
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_trials_job_id ON optimization_trials(job_id);
CREATE INDEX idx_trials_sharpe ON optimization_trials((metrics->>'sharpe')::FLOAT DESC);
```

## Files Involved

### Create
- `src/agentfx/api/routers/optimization.py` - REST endpoints (plural resource: `/optimization-jobs`)
- `src/agentfx/api/models/optimization.py` - SQLAlchemy models (OptimizationJob, OptimizationTrial)
- `src/agentfx/api/schemas/optimization.py` - Pydantic schemas (*Base/*Create/*Public pattern)
- `src/agentfx/api/services/optimization.py` - Business logic service
- `alembic/versions/xxx_add_optimization_tables.py` - Database migration
- `tests/unit/agents/test_bayesian_optimizer.py` - Unit tests
- `tests/integration/agents/test_optimizer_integration.py` - Integration tests
- `tests/contracts/test_optimization_api.py` - Contract tests

### Modify
- `src/agentfx/agents/optimizer.py` - Replace grid search with Bayesian optimization
- `src/agentfx/services/backtest.py` - Add `load_price_df_from_db()` to read from PostgreSQL
- `src/agentfx/api/main.py` - Register optimization router

## Dependencies

- Existing candle ingestion worker (`src/agentfx/api/workers/candle_ingestion.py`)
- Existing backtest engine (`src/agentfx/services/backtest.py`)
- Strategy implementations (`src/agentfx/core/strategy.py`)
- Database infrastructure (`src/agentfx/api/core/database.py`)
- scikit-learn>=1.3.0 (GaussianProcessRegressor for Bayesian optimization)
- scipy>=1.11.0 (Expected Improvement calculation)

## Notes

- Bayesian optimization is primary approach (more efficient than grid search)
- Use scikit-learn GaussianProcessRegressor with Matern kernel
- Expected Improvement acquisition function to select next parameter set
- Agent auto-determines iteration count: 10 random + ~10-15 per parameter dimension
- Gate B thresholds are informational only in v0.2.0 (enforcement in v0.3.0 Validator)
- Backtest service must read from PostgreSQL candles table (v0.1.0 infrastructure)
