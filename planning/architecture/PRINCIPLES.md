# Architectural Principles

- Security-first
- Deterministic and reproducible research
- Separation of concerns (data, research, execution, risk, monitoring)
- Observability by design (logs, metrics, traces, lineage)
- Safe rollouts (paper → live, circuit breakers)
- Compliance and auditability
- Modular, scalable, and cost-aware
- Modularity and clear boundaries (stable public interfaces; minimize blast radius)
- Isolated environments (all dev/test/build in virtualenvs with pinned deps)

Agentic additions:
- Goal- and policy-driven autonomy (objective hierarchy; enforce explicit policies)
- Safe tool-use via capability-scoped interfaces and rate limits
- Human-in-the-loop override and kill switch to flat positions
- Alignment and guardrails: pre/post-decision risk and compliance checks
- Evaluate–learn–deploy separation; only validated immutable artifacts run live
- Reproducible agency: provenance for each decision (model/version, inputs signature, seed)
- Bounded exploration budget; exploration defaults to Paper, not Live
- Privacy and least privilege by default
