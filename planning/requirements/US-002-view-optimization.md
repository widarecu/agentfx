# US-002: User Views Optimization Results

**Version:** v0.2.0 - Parameter Optimizer Agent
**Priority:** P0 - Must Have
**Status:** TODO

## Given-When-Then

**GIVEN** optimization runs have completed
**WHEN** the user views the Optimization page in the portal
**THEN** they see:
1. List of optimization jobs with status (queued, running, completed)
2. Top results for each completed job ranked by Sharpe ratio
3. Drill-down capability to view individual experiment details

## Acceptance Criteria

- [ ] Optimization list page shows all jobs for current user
- [ ] Jobs display: strategy, pair, status, progress %, created date
- [ ] Completed jobs show top 3 results with key metrics
- [ ] Click-through to job detail shows all experiments
- [ ] Experiment detail shows full backtest metrics and params
- [ ] Pagination for large result sets

## API Contract

### List Jobs
```
GET /api/v1/optimize
Authorization: Bearer <token>

Response:
{
  "jobs": [
    {
      "job_id": "opt-abc123",
      "strategy": "ma",
      "pair": "EURUSD",
      "status": "completed",
      "progress": 100,
      "total_combinations": 90,
      "completed_combinations": 90,
      "created_at": "2025-01-15T10:00:00Z",
      "top_sharpe": 1.85
    }
  ],
  "total": 15,
  "page": 1,
  "per_page": 10
}
```

### Get Job Detail
```
GET /api/v1/optimize/{job_id}
Authorization: Bearer <token>

Response:
{
  "job_id": "opt-abc123",
  "strategy": "ma",
  "pair": "EURUSD",
  "param_ranges": {...},
  "status": "completed",
  "top_results": [...],
  "all_experiments": [
    {
      "experiment_id": "exp-xyz789",
      "params": {"fast": 10, "slow": 50},
      "metrics": {...}
    },
    ...
  ]
}
```

### Get Experiment Detail
```
GET /api/v1/experiments/{experiment_id}
Authorization: Bearer <token>

Response:
{
  "experiment_id": "exp-xyz789",
  "job_id": "opt-abc123",
  "strategy": "ma",
  "pair": "EURUSD",
  "params": {"fast": 10, "slow": 50},
  "metrics": {
    "sharpe": 1.85,
    "max_drawdown": 0.06,
    "hit_rate": 0.54,
    "trade_count": 342,
    "total_return": 0.15,
    "annualized_return": 0.32,
    "win_rate": 0.54,
    "avg_win": 0.0025,
    "avg_loss": -0.0018
  },
  "data_start": "2024-07-01T00:00:00Z",
  "data_end": "2024-12-31T00:00:00Z",
  "created_at": "2025-01-15T10:05:00Z"
}
```

## Verification Commands

```bash
# API endpoint tests
PYTHONPATH=src pytest tests/unit/routers/test_optimize_router.py -v

# Frontend component tests
cd portal/web && npm run test -- --grep "Optimization"

# E2E test - view optimization results
cd portal/web && npx playwright test tests/e2e/optimization.spec.ts
```

## UI Components

### Optimization List Page (`/optimization`)
```
+--------------------------------------------------+
| Optimization Jobs                        [+ New] |
+--------------------------------------------------+
| Strategy | Pair   | Status    | Progress | Sharpe|
|----------|--------|-----------|----------|-------|
| MA       | EURUSD | Completed | 100%     | 1.85  |
| RSI      | GBPUSD | Running   | 45%      | -     |
| Donchian | USDJPY | Queued    | 0%       | -     |
+--------------------------------------------------+
```

### Job Detail Page (`/optimization/{job_id}`)
```
+--------------------------------------------------+
| MA Crossover - EURUSD                            |
| Status: Completed | 90/90 combinations           |
+--------------------------------------------------+
| Top Results                                      |
| #1: fast=10, slow=50 | Sharpe: 1.85 | MDD: 6%   |
| #2: fast=15, slow=60 | Sharpe: 1.72 | MDD: 7%   |
| #3: fast=10, slow=40 | Sharpe: 1.65 | MDD: 5%   |
+--------------------------------------------------+
| All Experiments (90)                    [Filter] |
| Params           | Sharpe | MDD    | Trades     |
|------------------|--------|--------|------------|
| fast=10, slow=50 | 1.85   | 6%     | 342        |
| fast=15, slow=60 | 1.72   | 7%     | 298        |
+--------------------------------------------------+
```

## Files Involved

### Create
- `portal/web/src/pages/Optimization.tsx` - Job list page
- `portal/web/src/pages/OptimizationDetail.tsx` - Job detail page
- `portal/web/src/components/ExperimentTable.tsx` - Results table
- `portal/web/src/services/optimizationApi.ts` - API client
- `tests/e2e/optimization.spec.ts` - E2E tests

### Modify
- `portal/web/src/router.tsx` - Add optimization routes
- `portal/web/src/components/Sidebar.tsx` - Add nav link

## Dependencies

- US-001: Optimization runs must exist to display results
- Auth context: User must be logged in

## Notes

- Use React Query for data fetching and caching
- Implement skeleton loading states
- Support sorting by any metric column
- Consider WebSocket for real-time progress updates on running jobs
