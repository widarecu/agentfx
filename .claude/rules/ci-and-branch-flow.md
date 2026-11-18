---
paths:
  - ".github/**"
  - "scripts/fleet_promote_pr.py"
  - "railway.toml"
  - "Dockerfile"
---

# CI, deploy and branch-flow files

These files control what reaches `main` and staging. Changes here are **Risk: Money-path** when they touch deploy gates: the PR never auto-merges; add `needs-wilson`.

Read `planning/runbooks/branch-flow.md` before changing any of:
- `agent-fleet-promote.yml` / `scripts/fleet_promote_pr.py`: keep exactly one live promote PR; promotes are merge commits, never squash.
- `main-to-fleet-backmerge.yml`: back-merges are merge commits; conflicts go to Wilson, never auto-resolved.
- `agent-fleet-state-on-promote.yml`: the only thing allowed to set Linear issues to Done (after a successful `Deploy`).

Deploy facts:
- `deploy.yml` runs on push to `main`: E2E (not currently a blocker: `needs: e2e` is commented out), then Railway (`railway up --detach --service agentfx`) and Vercel (`vercel deploy --prod`).
- CI E2E still uses a GitHub Actions Postgres service on 5432 and runs uvicorn/vite on 8000/5173, pinned with `PLAYWRIGHT_BASE_URL` and `API_BASE_URL`. Local E2E uses the Docker test profile (5174/8001). Moving CI to `make e2e` is a pending follow-up.
- The `Dockerfile` is the single production image, also used locally by compose. It runs `alembic upgrade head`, `python scripts/seed_instruments.py`, then `uvicorn agentfx.api.main:app`.
- The `RAILWAY_TOKEN` secret needs a Railway **Project** token, not an Account token; both give the same `Invalid RAILWAY_TOKEN` error.
