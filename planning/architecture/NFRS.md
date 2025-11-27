# Non-Functional Requirements (Initial)

- Latency
  - Research/backtest: batch; not time-critical
  - Paper trade decision loop: p95 < 1s end-to-end
  - Live trade decision loop: target p95 < 500ms end-to-end
- Availability
  - Paper: 99.0% monthly
  - Live execution core: 99.5% monthly
- Reliability
  - Idempotent order handling; retry with backoff
  - Circuit breaker on broker/API errors
- Performance
  - Backtest throughput sufficient to evaluate 100 strategies/day
- Data
  - Versioned datasets and configs; reproducible runs
  - Retention: raw market data ≥ 90 days; derived features ≥ 30 days (MVP)
- Security
  - Secrets via env/secret store; no secrets in repo
  - Principle of least privilege for broker/data keys
- Observability
  - Metrics: Sharpe, MDD, exposure, turnover; infra: CPU/mem, API errors, latency
  - Audit trail for orders and strategy versions

Agentic additions:
- Decision provenance logging must fit within end-to-end latency SLOs
- Policy engine latency: p99 < 50ms per decision
- Provenance retention ≥ 90 days (MVP), partitioned by environment (dev/paper/live)
- Only validated artifacts may be promoted: Gate B evidence required for rollout
- Environment separation for configs, secrets, and artifacts; no cross-environment mutation
- Rate limiting: enforce global and per-pair order-rate limits aligned with MVP constraints
