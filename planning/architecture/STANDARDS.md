# Engineering Standards (Initial)

- Config via environment files and typed config objects
- Data/experiments: versioned; record seeds and code hash
- Testing: unit + integration tests required for PR merge
- Code style: formatter + linter; pre-commit recommended
- Documentation: update FEAT and runbooks with changes

Agentic additions:
- Central policy engine: risk/exposure/order-rate/instrument/time-window checks pre-order
- Decision provenance: persist strategy/agent version, inputs signature, policy results, action, timings
- Versioning and rollout: immutable strategy IDs; promotion only via Gate B with validation evidence
- Feature flags: enable/disable strategies, exploration mode, environment stage (dev/paper/live)
- Rate limiting and concurrency: enforce per-pair and global limits in adapters
- Experiment hygiene: time-series CV, OOS holdout, leakage checks are mandatory
- Operational safety: standardized retries/backoff and circuit breaker usage
- Config discipline: typed configs, environment separation; no dynamic code execution in Live
- Observability baseline: dashboards for agent decisions, policy denials, order lifecycle, latency histograms

 Modularity and environment standards:
 - Repository layout
   - src/agentfx/core: domain and policies (pure, no I/O)
   - src/agentfx/adapters: external systems (broker, storage, observability)
   - src/agentfx/services: orchestration (ingestion, backtest, paper runner)
   - src/agentfx/shared: config, types, helpers
   - src/agentfx/ui: user portal (later)
   - tests/: mirror module layout with unit/integration
 - Interfaces/ABCs for broker, datastore, metrics; adapters implement and are injected at edges
 - Virtual environments mandatory for dev/test/build (no system Python)
 - Dependency management
   - requirements.txt (prod)
   - requirements-dev.txt (dev: pytest, coverage, ruff, black, mypy, tox)
   - Optional pip-tools lockfiles later
 - Tooling
   - pytest + coverage; ruff + black; mypy (incremental)
   - tox/nox to reproduce CI locally
 - Makefile targets
   - make venv (python -m venv .venv)
   - make install (pip install -r requirements.txt)
   - make dev (pip install -r requirements.txt -r requirements-dev.txt)
   - make test, make lint, make typecheck, make format
