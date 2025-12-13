# Non-Functional Requirements

This document defines measurable SLOs, performance targets, and operational requirements for AgentFX.

---

## Service Level Objectives (SLOs)

### Latency

| Operation | Environment | Target | Maximum |
|-----------|-------------|--------|---------|
| Decision loop (end-to-end) | Paper | p95 < 1s | p99 < 2s |
| Decision loop (end-to-end) | Live | p95 < 500ms | p99 < 1s |
| Policy engine check | All | p95 < 20ms | p99 < 50ms |
| API response (simple) | All | p95 < 100ms | p99 < 200ms |
| API response (complex) | All | p95 < 500ms | p99 < 1s |
| WebSocket message | All | p95 < 50ms | p99 < 100ms |

### Availability

| Service | Environment | Monthly Target |
|---------|-------------|----------------|
| API Server | Development | 95.0% |
| API Server | Paper | 99.0% |
| API Server | Live | 99.5% |
| WebSocket Streaming | Paper | 99.0% |
| WebSocket Streaming | Live | 99.5% |
| Database | All | 99.9% |

### Throughput

| Operation | Target | Notes |
|-----------|--------|-------|
| Backtest strategies/day | 100+ | Batch processing |
| Optimization runs/hour | 50+ | Per user |
| Market data ticks/second | 100+ | Per pair |
| Concurrent WebSocket connections | 100+ | Per server |

---

## Data Requirements

### Retention

| Data Type | Minimum Retention | Notes |
|-----------|-------------------|-------|
| Raw market data | 90 days | 1-minute candles |
| Derived features | 30 days | Calculated indicators |
| Decision provenance | 90 days | All trading decisions |
| Audit logs | 1 year | Security events |
| Experiment results | 180 days | Backtest results |
| User sessions | 30 days | JWT tokens |

### Integrity

| Requirement | Standard |
|-------------|----------|
| Data versioning | All datasets versioned with hash |
| Config versioning | All configs versioned with hash |
| Reproducibility | Backtest runs reproducible with same seed |
| Backup frequency | Daily (database), hourly (critical) |

---

## Reliability

### Error Handling

| Scenario | Response |
|----------|----------|
| Broker API timeout | Retry with exponential backoff (max 3) |
| Broker API error (4xx) | Log, alert, return error |
| Broker API error (5xx) | Retry with backoff, circuit breaker |
| Database connection lost | Reconnect with backoff |
| WebSocket disconnect | Auto-reconnect with backoff |

### Circuit Breakers

| Trigger | Action | Recovery |
|---------|--------|----------|
| 5 consecutive broker failures | Halt trading for pair | Manual reset or 15min cooldown |
| p95 latency > 2x SLO for 10min | Reduce load | Auto-recover when SLO met |
| 2% daily loss | Halt all trading | Next trading day |
| 10% max drawdown | System halt | Manual review required |

---

## Security Requirements

### Authentication

| Requirement | Standard |
|-------------|----------|
| Password storage | Argon2 hashing |
| Token type | JWT (HS256 or RS256) |
| Token expiration | 30 minutes (configurable) |
| Refresh tokens | 7 days (configurable) |
| Failed login lockout | 5 attempts, 15min lockout |

### Authorization

| Role | Capabilities |
|------|--------------|
| VIEWER | Read market data, view dashboards |
| TRADER | VIEWER + execute paper trades, run backtests |
| ADMIN | TRADER + manage users, promote to live |

### Secrets Management

| Requirement | Standard |
|-------------|----------|
| Storage | Environment variables or secret manager |
| Rotation | Every 90 days for API keys |
| Access | Least privilege principle |
| Logging | Never log secrets |

---

## Observability Requirements

### Metrics (Required)

| Category | Metrics |
|----------|---------|
| Trading | Sharpe ratio, max drawdown, hit rate, exposure |
| Performance | API latency (p50, p95, p99), throughput |
| Infrastructure | CPU, memory, disk, network |
| Errors | Error rate by type, 4xx/5xx counts |
| Business | Active users, trades/day, strategies running |

### Logging (Required)

| Event | Log Level | Required Fields |
|-------|-----------|-----------------|
| Trading decision | INFO | strategy_id, pair, signal, reason |
| Policy denial | WARN | strategy_id, policy, reason |
| Order execution | INFO | order_id, pair, side, price, status |
| Error | ERROR | error_type, message, stack_trace |
| Security event | WARN/ERROR | user_id, action, ip_address |

### Dashboards (Required)

| Dashboard | Key Visualizations |
|-----------|-------------------|
| Trading Overview | P&L, positions, recent trades |
| Strategy Performance | Sharpe trend, drawdown, win rate |
| System Health | Latency histograms, error rates |
| Agent Activity | Optimization jobs, validation results |

---

## Performance Testing

### Benchmarks

| Test | Target | Frequency |
|------|--------|-----------|
| API load test (100 req/s) | p95 < 200ms | Pre-release |
| Backtest throughput | 100 strategies/day | Weekly |
| WebSocket stress (100 clients) | No drops | Pre-release |
| Database query performance | Critical queries < 100ms | Weekly |

### Profiling

| Area | Tool | Threshold |
|------|------|-----------|
| Python CPU | cProfile | No function > 100ms |
| Memory | memory_profiler | No leaks detected |
| Database | EXPLAIN ANALYZE | No full table scans |

---

## Quick Reference Card

```
LATENCY TARGETS
---------------
Paper decision: p95 < 1s
Live decision:  p95 < 500ms
Policy check:   p99 < 50ms
API simple:     p95 < 100ms

AVAILABILITY TARGETS
--------------------
Paper: 99.0% monthly
Live:  99.5% monthly

TRADING LIMITS (from MVP_CONSTRAINTS.md)
----------------------------------------
Risk per trade:    <= 0.5%
Daily loss limit:  2% (auto-halt)
Max drawdown:      10% (system halt)
Max positions:     5 concurrent
Order rate:        <= 10/min

GATE B THRESHOLDS
-----------------
Sharpe:     >= 1.0
Max DD:     <= 10%
Hit rate:   >= 48%
Min trades: >= 300 in OOS
```
