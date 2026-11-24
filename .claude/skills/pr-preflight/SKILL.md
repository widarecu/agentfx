---
name: pr-preflight
description: Checks to run before opening or updating a PR in this repo. Use before every `gh pr create`, and whenever asked whether a branch is ready.
---

# Pre-PR checks

Run from the repo root. All must pass before opening the PR.

1. `make lint`
2. `make test-unit`
3. If you touched DB code or migrations: `make docker-up PROFILE=test`, then `make test-integration`.
4. If you added or changed a setting in `src/agentfx/api/core/config.py`: `make check-env-example`.
5. If you touched a migration: confirm a single Alembic head against `origin/main` too (see `.claude/rules/migrations.md`).
6. If you touched Settings or opt-in-driven portal UI: `make check-settings-vitest`, and a selector test exists for every branch (`.claude/rules/portal-ui-testing.md`).
7. If you fixed a bug: a test reproduces it (fails before, passes after).
8. Diff sanity:
   ```bash
   git fetch origin main
   git diff --stat origin/main...HEAD
   ```
   Only files the issue needs, plus their tests. No `.env.*` files except `.env.example`.
9. PR targets `main` (never a direct push). Its title and branch name exactly one AFX ticket, the one it closes; other tickets appear in the body only without "Fixes/Closes/Resolves/Part of/Related to/Refs" in front. Label it `agent-authored`, whatever the risk; `needs-wilson` is only for real blockers. See `planning/runbooks/delivery-pipeline.md`.

Report what you ran and the result. If something fails after three honest attempts, stop and report the failing output rather than pushing a red branch.
