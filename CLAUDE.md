# CLAUDE.md

AgentFX is an autonomous FX strategy factory: agents define, build, backtest, validate and evolve trading strategies against OANDA, paper-first, with Wilson's approval before anything trades live. Wilson is the only human, the only approver, and the operator rather than a full-time developer: explain in plain terms, define jargon, describe behaviour rather than code.

Success means **profitable, live-ready strategies**, not PRs merged or experiments run. Tie every piece of work to that.

## Hard rules

1. **Only the `test` Docker profile may be wiped.** Never run `make e2e` or `docker compose down -v` against `dev` or `uat`; uat holds Wilson's real data. ([environments](planning/runbooks/environments.md))
2. **One branch: `main`. Never push to it.** Claim with `python3 scripts/afx.py start AFX-<n>` (no `LINEAR_API_KEY`, as in a Devin session: Linear MCP `get_issue`; if it isn't **Ready**, stop and report, otherwise `save_issue` with `state: "In Progress"` and `assignee: "me"`; A session the PE dispatched for the ticket skips the claim: the PE already claimed it), branch `agent/AFX-<n>-<slug>` off `main`, open one PR into `main` labelled `agent-authored`, then stop: merge, deploy and Done are automatic. Every merge to `main` deploys to staging. Blocked? `python3 scripts/afx.py block AFX-<n> "question"` (no key: `save_issue` with `state: "Blocked"` and `addLabels: ["needs-wilson"]`, then one `save_comment` with the question). ([delivery pipeline](planning/runbooks/delivery-pipeline.md))
3. **Money-path changes** (order execution, sizing, kill switch, credentials, deploy gates) **need Wilson's approval before they merge.** A Money-path ticket's PR waits in `pr-policy` for his approval of its latest commit, and any PR touching a path in `.github/CODEOWNERS` needs his code-owner review. Still add `agent-authored`; don't add `needs-wilson` for risk alone, it is for real blockers.
4. **OANDA practice keys only.**
5. **Never edit `.env.development`, `.env.test`, `.env.uat`.** Any new setting in `src/agentfx/api/core/config.py` goes into `.env.example` in the same PR.
6. **Linear is the work tracker** (team `AFX`). Work only issues in **Ready**; file new issues only into **Triage**; never set priority or change approved scope. `planning/BACKLOG.md` is frozen. Skills: `work-issue`, `file-issue`.
7. **A PR names exactly one AFX ticket, the one it closes, in its title and branch.** `pr-policy` fails any other PR, and deploy sync closes the ticket the title names. Linear also reads the body: "Fixes/Closes/Resolves AFX-n" closes that ticket and "Part of/Related to/Refs AFX-n" links it, so mention other tickets only when needed, without those words.
8. **Before PR:** `make lint` and `make test-unit` green (skill `pr-preflight`).
9. **Blocked on a product decision?** `python3 scripts/afx.py block AFX-<n> "question"` (Blocked + `needs-wilson` + the question; the Linear MCP path is in rule 2), then stop. Don't guess.
10. **Verify before you plan.** A ticket or doc saying "not done" is a starting point: grep the code first. Shipped features with stale tickets are common.
11. **Paper trading is the aggressive sandbox.** No allocation caps or auto-halts on paper; the only guardrail there is OANDA's margin call. The risk limits in `planning/MVP_CONSTRAINTS.md` apply to live.

## Commands

Always use `make`; it sets `ENVIRONMENT=test` and `PYTHONPATH=src`. Never call `pytest` or `vitest` directly (a hook refuses it).

| Task | Command |
|---|---|
| Unit tests (backend + portal) | `make test-unit` |
| Integration tests | `make docker-up PROFILE=test` then `make test-integration` |
| E2E (test profile only) | `make e2e` |
| One file / pattern | `make test-file FILE=...` / `make test-pattern PATTERN=...` |
| Lint / format / types | `make lint` / `make format` / `make typecheck` |
| Start / stop a stack | `make docker-up PROFILE=dev` / `make docker-down PROFILE=dev` |
| Rebuild after backend changes | `make docker-rebuild PROFILE=dev` (`--force-recreate` alone reuses the old image) |
| Logs | `make docker-logs PROFILE=dev` |
| Backtest | `make backtest PAIRS="EURUSD" DAYS=2` |

Profiles: `dev` (8000 / 5173 / pg 5432), `test` (8001 / 5174 / pg 5433, wiped by E2E), `uat` (8002 / 5175 / pg 5434, never wiped).

## Repo map

- `src/agentfx/core/`: pure domain logic: `policy.py` risk checks, `gates.py` and `validation.py` validation gates, `dsl/` strategy language and indicators, `metrics.py`.
- `src/agentfx/agents/`: `optimizer.py`, `validator.py`, `orchestrator.py`, `evolution/` (genetic + LLM hypothesis search), `fleet/` (the autonomous PM, QA and PE agents, Linear and Managed Agents wiring).
- `src/agentfx/services/`: backtest, ingestion, paper trading (`paper_*.py`), CLI.
- `src/agentfx/api/`: FastAPI app; `core/config.py` holds all settings.
- `src/agentfx/adapters/`: OANDA and storage. `notifications/`: email (SendGrid).
- `portal/web/src/`: React + TypeScript portal. `migrations/`: Alembic.
- `planning/`: `ROADMAP.md`, `MVP_CONSTRAINTS.md` (Gate B thresholds, risk limits), `architecture/`, `requirements/`, `runbooks/`.

## Read before changing

Claude Code loads the `.claude/rules/` files automatically when you touch matching paths. **Any other agent must read the matching file before changing that area.**

| If you are changing | Read |
|---|---|
| Tests, fixtures, seed scripts | `.claude/rules/testing.md`, `tests/README.md` |
| Portal components, especially Settings | `.claude/rules/portal-ui-testing.md` |
| Settings, `.env.example`, docker-compose, ingestion | `.claude/rules/ingestion-config.md` |
| Migrations or ORM models | `.claude/rules/migrations.md` |
| `.github/`, Dockerfile, deploy scripts | `.claude/rules/ci-and-branch-flow.md`, `planning/runbooks/delivery-pipeline.md` |
| Docker profiles, local data | `planning/runbooks/environments.md` |
| Linear workflow, ticket states | `planning/runbooks/delivery-pipeline.md`, `planning/runbooks/linear-operating-model.md` |
| Strategy validation, risk limits | `planning/MVP_CONSTRAINTS.md` |
| PE agent / Managed Agents setup | `planning/runbooks/pe-managed-agents-setup.md` |
| Writing a user story | `planning/README.md` |
