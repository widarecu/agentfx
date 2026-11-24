---
paths:
  - ".github/**"
  - "scripts/deploy_staging.py"
  - "scripts/railway_deploy.py"
  - "scripts/pr_policy.py"
  - "railway.toml"
  - "Dockerfile"
---

# CI, deploy and branch-flow files

These files control what reaches `main` and staging. Changes that touch deploy gates are **Risk: Money-path**, and `.github/` is in `.github/CODEOWNERS`, so Wilson reviews them before they merge (CLAUDE.md rule 3).

Read `planning/runbooks/delivery-pipeline.md` before changing any of:
- `ci.yml`: the only PR gate; `CI required` must fail when any job fails. The OANDA practice key stays out of PR runs.
- `pr-policy.yml` / `scripts/pr_policy.py` / `pr-review-relay.yml`: one AFX ticket per PR, ticket state, Money-path approval, squash auto-merge only for `agent-authored`.
- `deploy.yml` "Sync tickets" (`scripts/afx.py deploy-sync`): the only thing allowed to set Linear issues to Done or (after a failed deploy) Blocked.
- `delivery-reconcile.yml`: daily replay of a missed sync plus the drift digest; it never moves other tickets.

Deploy facts:
- `deploy.yml` runs on every push to `main` (each merged PR): plan, then Railway (`scripts/railway_deploy.py`: `railway up --detach`, then polls the deployment until Railway reports it built and healthy, so a failed build or health check fails the job) and Vercel (`vercel deploy --prod`), then staging verification, then the `deployed/staging` tag and ticket sync.
- CI E2E still uses a GitHub Actions Postgres service on 5432 and runs uvicorn/vite on 8000/5173, pinned with `PLAYWRIGHT_BASE_URL` and `API_BASE_URL`. Local E2E uses the Docker test profile (5174/8001). Moving CI to `make e2e` is a pending follow-up.
- The `Dockerfile` is the single production image, also used locally by compose. It runs `alembic upgrade head`, `python scripts/seed_instruments.py`, then `uvicorn agentfx.api.main:app`.
- The `RAILWAY_TOKEN` secret needs a Railway **Project** token, not an Account token; both give the same `Invalid RAILWAY_TOKEN` error.
