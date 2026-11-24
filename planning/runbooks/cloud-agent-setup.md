# Cloud coding agent setup (AFX-6)

Lets a Claude Code cloud session clone this repo, run its tests, and work issues from the Linear **Ready** queue — so you can approve the result from your phone without opening the laptop. The delivery flow it follows is [delivery-pipeline.md](delivery-pipeline.md).

## 1. One-time setup Wilson does

### 1a. In the cloud agent provider (Claude Code cloud / similar)

Create an environment for this repo (`widarecu/agentfx`) and set **these secrets by name**:

| Env var | What it's for | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | the cloud agent's own LLM calls | `console.anthropic.com → API keys` |
| `LINEAR_API_KEY` | `.mcp.json`'s Linear server, and `scripts/afx.py start/block` | `linear.app → Settings → API → Personal API keys` |
| `GITHUB_TOKEN` | push `agent/AFX-*` branches + open PRs on this repo | GitHub → Settings → Developer settings → Personal access tokens → Fine-grained; scope: `widarecu/agentfx` only; permissions: `Contents: write`, `Pull requests: write`, `Metadata: read` |
| `OANDA_API_KEY` | local integration tests that hit OANDA (optional: they skip without it, as on PR CI) | OANDA practice account (**never** live) |
| `OANDA_ACCOUNT_ID` | ditto | same OANDA practice account |

Deliberately not set (the cloud agent must not touch these):

| Env var | Why withheld |
|---|---|
| `RAILWAY_TOKEN` | deploys only run from `main` CI, never from an agent branch |
| `FERNET_KEY` | encrypts user credentials at rest; only the deployed app needs it |
| `SMTP_PASSWORD` / SendGrid key | notifications go via the deployed app, not the agent |
| Any `*_UAT` or `*_PRODUCTION` database URL | agent only runs the `test` profile |

### 1b. In GitHub (one click)

Repo → Settings → General → Pull Requests → turn on **Allow auto-merge**. `pr-policy` uses it to squash-merge passing `agent-authored` PRs into `main`.

### 1c. In the Linear workspace

Nothing — the GitHub integration is already live, and `.mcp.json` points at `https://mcp.linear.app/mcp` which uses `LINEAR_API_KEY` from env.

## 2. What the cloud agent needs to be able to do, verified in-session

The agent should be able to run these from a fresh clone, all green:

```bash
# Env sanity
test -n "$ANTHROPIC_API_KEY" -a -n "$LINEAR_API_KEY" -a -n "$GITHUB_TOKEN" \
  || echo "MISSING SECRET"

# Install
make dev                 # creates venv, installs dev deps + pre-commit

# Fast gate (no Docker)
make test-unit           # backend pytest (-m unit) + frontend Vitest

# Slow gate (needs Docker — most cloud providers give you docker-in-docker)
make docker-up PROFILE=test
make test-integration    # pytest -m "integration and not slow" against Postgres on :5433
make docker-down PROFILE=test
```

`make test-unit` alone is the pre-PR gate the operating model requires (per CLAUDE.md hard rule 8). `test-integration` is needed only when the change touches DB code.

## 3. First trial

Pick a small Ready issue and follow [delivery-pipeline.md](delivery-pipeline.md) §1 end to end:

1. `python3 scripts/afx.py start AFX-N` moves it Ready → In Progress.
2. Branch `agent/AFX-N-slug` off `main`, implement, run `make lint` and `make test-unit`.
3. Open a PR into `main` with `AFX-N` in the title and label it `agent-authored` → Linear moves it to **In Review**.
4. `pr-policy` passes once `CI required` is green (Money-path: once Wilson approves the latest commit) and squash-merges it.
5. The merge deploys to staging; deploy sync moves the ticket to **Done** after staging verification (or Blocked + `needs-wilson` if the deploy fails).

## 4. Troubleshooting

- **Agent picks up a Money-path issue**: that's fine. Its PR still gets `agent-authored`; `pr-policy` holds it until Wilson approves the latest commit, then merges it.
- **`make test-integration` fails with "could not connect to server"**: Docker-in-Docker not enabled on the cloud agent provider. Fall back to the agent provider's hosted Postgres and set `DATABASE_URL` manually; the test profile uses port 5433 by default.
- **Linear MCP times out**: usually means `LINEAR_API_KEY` isn't set in the agent env. Check with `env | grep LINEAR_` (never log the value itself).
- **GitHub push rejected (403)**: `GITHUB_TOKEN` scope is wrong. Fine-grained tokens need `Contents: write` + `Pull requests: write` on this repo specifically.

## 5. Off-limits (same as every agent)

- `dev` / `uat` profiles → **test** profile only.
- Live OANDA keys → **practice** only.
- Pushing to `main` → never; always through a PR that passes `CI required` and `pr-policy`.
- `needs-wilson` issues → leave them even if Ready.
