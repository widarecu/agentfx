# US-002: User Views Optimization Results

**Version:** v0.2.0 - Bayesian Parameter Optimizer Agent
**Priority:** P0 - Must Have
**Status:** TODO

## Given-When-Then

**GIVEN** optimization jobs have been created and some have completed
  **AND** the user is authenticated (VIEWER, TRADER, or ADMIN role)
**WHEN** the user navigates to the Optimization page in the portal
**THEN** they see:
1. List of optimization jobs with status (queued, running, completed, failed)
2. Progress indicator for running jobs
3. Top 3 results for completed jobs with key metrics and Gate B pass/fail status
4. Click-through capability to view individual trial details

## Acceptance Criteria

- [ ] Optimization list page shows all jobs accessible to current user
- [ ] Jobs display: Job ID, strategy, pair, status badge, progress %, best Sharpe, created date
- [ ] Status badges: queued (gray), running (blue pulse), completed (green), failed (red)
- [ ] Running jobs show progress bar (completed_iterations / estimated_iterations)
- [ ] Completed jobs show top 3 results cards with key metrics
- [ ] Click-through to job detail shows all trials in sortable/filterable table
- [ ] Trial detail shows full backtest metrics and params
- [ ] Pagination for large result sets
- [ ] Authorization: VIEWER+ role required

## API Contract

### List All Jobs
```
GET /api/v1/optimization-jobs
Authorization: Bearer <JWT_TOKEN>

Query Parameters:
- page (default: 1)
- per_page (default: 20)
- status (optional filter)
- strategy (optional filter)

Response:
{
  "items": [
    {
      "id": "optjob_20250115_abc123",
      "strategy": "ma",
      "pair": "EUR_USD",
      "status": "completed",
      "progress": 100,
      "estimated_iterations": 55,
      "completed_iterations": 55,
      "best_sharpe": 1.85,
      "created_at": "2025-01-15T10:00:00Z",
      "completed_at": "2025-01-15T10:15:00Z"
    },
    {
      "id": "optjob_20250115_def456",
      "strategy": "rsi",
      "pair": "GBP_USD",
      "status": "running",
      "progress": 45,
      "estimated_iterations": 40,
      "completed_iterations": 18,
      "best_sharpe": null,
      "created_at": "2025-01-15T11:00:00Z",
      "completed_at": null
    }
  ],
  "total": 15,
  "page": 1,
  "per_page": 20
}
```

### Get Job Detail with Top Results
```
GET /api/v1/optimization-jobs/{job_id}
Authorization: Bearer <JWT_TOKEN>

Response:
{
  "id": "optjob_20250115_abc123",
  "strategy": "ma",
  "pair": "EUR_USD",
  "param_ranges": {
    "fast": {"min": 5, "max": 50},
    "slow": {"min": 20, "max": 200}
  },
  "status": "completed",
  "estimated_iterations": 55,
  "completed_iterations": 55,
  "data_start": "2020-01-01T00:00:00Z",
  "data_end": "2025-01-01T00:00:00Z",
  "created_at": "2025-01-15T10:00:00Z",
  "completed_at": "2025-01-15T10:15:00Z",
  "top_results": [
    {
      "rank": 1,
      "trial_id": "trial_optjob_20250115_abc123_042",
      "params": {"fast": 10, "slow": 50},
      "metrics": {
        "sharpe": 1.85,
        "max_drawdown": 0.06,
        "hit_rate": 0.54,
        "trade_count": 342,
        "total_return": 0.45,
        "profit_factor": 1.8
      },
      "passes_gate_b": true,
      "gate_b_failures": []
    },
    {
      "rank": 2,
      "trial_id": "trial_optjob_20250115_abc123_028",
      "params": {"fast": 15, "slow": 60},
      "metrics": {
        "sharpe": 1.72,
        "max_drawdown": 0.07,
        "hit_rate": 0.52,
        "trade_count": 298,
        "total_return": 0.38,
        "profit_factor": 1.6
      },
      "passes_gate_b": true,
      "gate_b_failures": []
    },
    {
      "rank": 3,
      "trial_id": "trial_optjob_20250115_abc123_015",
      "params": {"fast": 12, "slow": 55},
      "metrics": {
        "sharpe": 1.65,
        "max_drawdown": 0.08,
        "hit_rate": 0.51,
        "trade_count": 310,
        "total_return": 0.35,
        "profit_factor": 1.5
      },
      "passes_gate_b": true,
      "gate_b_failures": []
    }
  ]
}
```

### List All Trials for a Job
```
GET /api/v1/optimization-jobs/{job_id}/trials
Authorization: Bearer <JWT_TOKEN>

Query Parameters:
- page (default: 1)
- per_page (default: 50)
- sort_by (default: sharpe)
- sort_order (default: desc)

Response:
{
  "items": [
    {
      "id": "trial_optjob_20250115_abc123_042",
      "params": {"fast": 10, "slow": 50},
      "metrics": {
        "sharpe": 1.85,
        "max_drawdown": 0.06,
        "hit_rate": 0.54,
        "trade_count": 342,
        "total_return": 0.45,
        "profit_factor": 1.8
      },
      "passes_gate_b": true,
      "gate_b_failures": [],
      "created_at": "2025-01-15T10:12:00Z"
    }
  ],
  "total": 55,
  "page": 1,
  "per_page": 50
}
```

### Get Trial Detail
```
GET /api/v1/optimization-jobs/{job_id}/trials/{trial_id}
Authorization: Bearer <JWT_TOKEN>

Response:
{
  "id": "trial_optjob_20250115_abc123_042",
  "job_id": "optjob_20250115_abc123",
  "strategy": "ma",
  "pair": "EUR_USD",
  "params": {"fast": 10, "slow": 50},
  "metrics": {
    "sharpe": 1.85,
    "max_drawdown": 0.06,
    "hit_rate": 0.54,
    "trade_count": 342,
    "total_return": 0.45,
    "profit_factor": 1.8,
    "annualized_return": 0.12,
    "avg_win": 0.0025,
    "avg_loss": -0.0018
  },
  "passes_gate_b": true,
  "gate_b_failures": [],
  "data_start": "2020-01-01T00:00:00Z",
  "data_end": "2025-01-01T00:00:00Z",
  "created_at": "2025-01-15T10:12:00Z"
}
```

## Authorization Matrix

| Action | Minimum Role |
|--------|-------------|
| List optimization jobs | VIEWER |
| View job detail | VIEWER |
| View trial detail | VIEWER |
| View trial signals | VIEWER |

## Verification Commands

```bash
# API endpoint tests
PYTHONPATH=src pytest tests/unit/routers/test_optimization_router.py -v

# Frontend component tests
cd portal/web && npm run test -- --grep "Optimization"

# E2E test - view optimization results
cd portal/web && npx playwright test tests/e2e/optimization.spec.ts
```

## UI Components

### Optimization List Page (`/optimization`)
```
+------------------------------------------------------------------+
| Optimization Jobs                                    [+ New Job] |
+------------------------------------------------------------------+
| Job ID          | Strategy | Pair    | Status    | Progress | Best Sharpe | Created       |
|-----------------|----------|---------|-----------|----------|-------------|---------------|
| optjob_...abc123| MA       | EUR_USD | Completed | 100%     | 1.85        | Jan 15, 10:00 |
| optjob_...def456| RSI      | GBP_USD | Running   | [====  ] | -           | Jan 15, 11:00 |
| optjob_...ghi789| Donchian | USD_JPY | Queued    | 0%       | -           | Jan 15, 11:30 |
+------------------------------------------------------------------+
| < Prev | Page 1 of 3 | Next >                                    |
+------------------------------------------------------------------+
```

### Job Detail Page (`/optimization/:jobId`)
```
+------------------------------------------------------------------+
| MA Crossover - EUR_USD                           [Cancel] [Delete]|
| Status: Completed | 55/55 trials | 15 min duration               |
| Data range: Jan 1, 2020 - Jan 1, 2025 (5 years)                  |
+------------------------------------------------------------------+

Top 3 Results
+-------------------+  +-------------------+  +-------------------+
| #1 BEST           |  | #2                |  | #3                |
| fast=10, slow=50  |  | fast=15, slow=60  |  | fast=12, slow=55  |
|-------------------|  |-------------------|  |-------------------|
| Sharpe: 1.85      |  | Sharpe: 1.72      |  | Sharpe: 1.65      |
| MDD: 6%           |  | MDD: 7%           |  | MDD: 8%           |
| Hit Rate: 54%     |  | Hit Rate: 52%     |  | Hit Rate: 51%     |
| Trades: 342       |  | Trades: 298       |  | Trades: 310       |
| Gate B: PASS [✓]  |  | Gate B: PASS [✓]  |  | Gate B: PASS [✓]  |
|                   |  |                   |  |                   |
| [View Chart]      |  | [View Chart]      |  | [View Chart]      |
+-------------------+  +-------------------+  +-------------------+

+------------------------------------------------------------------+
| All Trials (55)                    [Sort: Sharpe ↓] [Filter]     |
+------------------------------------------------------------------+
| # | Params           | Sharpe | MDD    | Hit Rate | Trades | Gate B |
|---|------------------|--------|--------|----------|--------|--------|
| 1 | fast=10, slow=50 | 1.85   | 6%     | 54%      | 342    | ✓      |
| 2 | fast=15, slow=60 | 1.72   | 7%     | 52%      | 298    | ✓      |
| 3 | fast=12, slow=55 | 1.65   | 8%     | 51%      | 310    | ✓      |
| ..| ...              | ...    | ...    | ...      | ...    | ...    |
+------------------------------------------------------------------+
```

## Files Involved

### Create
- `portal/web/src/pages/Optimization.tsx` - Job list page
- `portal/web/src/pages/OptimizationDetail.tsx` - Job detail page with top 3 cards
- `portal/web/src/components/TrialTable.tsx` - Sortable/filterable trials table
- `portal/web/src/components/MetricsCard.tsx` - Top result card with Gate B indicator
- `portal/web/src/services/optimizationApi.ts` - API client
- `portal/web/src/mocks/optimizationMocks.ts` - Mock data for development
- `tests/e2e/optimization.spec.ts` - E2E tests

### Modify
- `portal/web/src/router.tsx` - Add optimization routes
- `portal/web/src/components/Sidebar.tsx` - Add "Optimization" nav link

## Dependencies

- US-001: Optimization jobs must exist to display results
- Auth context: User must be logged in
- `@tanstack/react-query` - Data fetching and caching

## Notes

- Use React Query for data fetching with automatic refetch for running jobs
- Implement skeleton loading states during data fetch
- Support sorting by any metric column in trials table
- Progress bar auto-updates every 5 seconds for running jobs
- Gate B pass/fail is informational (green checkmark / red X)
- "View Chart" button navigates to trial visualization (US-015)
