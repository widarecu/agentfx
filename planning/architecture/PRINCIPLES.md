# Architectural Principles

These principles guide all development decisions in AgentFX. SDLC agents must adhere to these when generating code, reviewing changes, or making architectural decisions.

---

## P1: Security First (MUST)

**DO:**
- Store all secrets via environment variables or secret manager
- Use least-privilege API keys; rotate every 90 days
- Log all broker API calls for audit
- Validate all inputs at system boundaries

**DO NOT:**
- Commit secrets, credentials, or API keys to repository
- Execute dynamic/user-provided code in production
- Expose internal error details to external users
- Store sensitive data in logs

---

## P2: Safe Rollouts (MUST)

**DO:**
- Paper trade for minimum 2 weeks before live deployment
- Require human approval for live trading promotion
- Implement circuit breakers on consecutive failures
- Use feature flags to enable/disable strategies

**DO NOT:**
- Deploy directly to live without paper validation
- Bypass Gate B thresholds for "quick fixes"
- Disable circuit breakers in production
- Allow autonomous promotion to live trading

---

## P3: Observability by Design (MUST)

**DO:**
- Log all trading decisions with full provenance (strategy, version, inputs, output)
- Emit metrics for Sharpe, drawdown, exposure, latency
- Create dashboards for agent decisions and policy denials
- Persist audit trail for minimum 90 days

**DO NOT:**
- Deploy code without logging capability
- Remove or reduce logging in production for "performance"
- Store provenance outside retention requirements

---

## P4: Separation of Concerns (SHOULD)

**DO:**
- Keep domain logic in `core/` (pure Python, no I/O)
- Keep external integrations in `adapters/`
- Keep autonomous agents in `agents/`
- Keep API concerns in `api/`

**DO NOT:**
- Mix I/O operations in domain logic
- Put business rules in API routers
- Couple agents directly to external APIs (use adapters)

---

## P5: Reproducibility (SHOULD)

**DO:**
- Version all datasets and configurations
- Record seeds, code hash, and model version for experiments
- Use immutable strategy IDs for deployment
- Implement time-series cross-validation with OOS holdout

**DO NOT:**
- Modify live strategies without creating new versions
- Use non-deterministic operations without logging seeds
- Skip leakage checks in backtesting

---

## P6: Bounded Autonomy (MUST for Agents)

**DO:**
- Enforce explicit risk policies via policy engine
- Implement rate limits on all autonomous operations
- Default exploration to Paper environment, never Live
- Provide human override and kill switch capability

**DO NOT:**
- Allow agents to bypass policy engine checks
- Execute Live trades without human approval
- Remove rate limits or concurrency controls
- Allow unbounded exploration budgets

---

## Quick Reference

| Principle | Level | Key Rule |
|-----------|-------|----------|
| Security First | MUST | No secrets in code; least privilege |
| Safe Rollouts | MUST | Paper first; human approval for live |
| Observability | MUST | Log everything; 90-day retention |
| Separation | SHOULD | Pure domain logic; adapters for I/O |
| Reproducibility | SHOULD | Version everything; immutable artifacts |
| Bounded Autonomy | MUST | Policy engine; human-in-the-loop |
