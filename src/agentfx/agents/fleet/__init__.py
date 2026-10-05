"""v0.8.x Agent Fleet — specialist Claude agents.

See `~/.claude/plans/i-think-we-need-functional-pony.md` (approved
2026-10-05) for the architectural plan and
`planning/ROADMAP.md` § v0.8.0 for the milestone exit criteria.

Package layout:
- `memory` — shared DB + filesystem helpers both the DB-side
  `agent_decisions` log and the markdown `agent_memory/` directory.
- `portfolio_manager` — PM agent (read-only pilot in v0.8.0).
- `quant_analyst` — QA agent (v0.8.0 Phase B).
- `platform_engineer` — PE agent via Managed Agents (v0.8.1).
- `research` — RA agent (v0.8.2).

The orchestrator in `agentfx.services.agent_orchestrator` dispatches
these agents on their cadence and enforces budgets.
"""
