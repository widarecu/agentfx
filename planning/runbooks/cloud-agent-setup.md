# Cloud coding agent setup (AFX-6)

Lets a Claude Code cloud session clone this repo, run its tests, and work issues from the Linear **Ready** queue — so you can approve the result from your phone without opening the laptop.

## 1. One-time setup Wilson does

### 1a. In the cloud agent provider (Claude Code cloud / similar)

Create an environment for this repo (`widarecu/agentfx`) and set **these secrets by name**:

| Env var | What it's for | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | the cloud agent's own LLM calls | `console.anthropic.com → API keys` |
| `LINEAR_API_KEY` | so `.mcp.json`'s Linear server can read/write AFX issues | `linear.app → Settings → API → Personal API keys` |
| `GITHUB_TOKEN` | push `agent/AFX-*` branches + open PRs on this repo | GitHub → Settings → Developer settings → Personal access tokens → Fine-grained; scope: `widarecu/agentfx` only; permissions: `Contents: write`, `Pull requests: write`, `Metadata: read` |
| `OANDA_API_KEY` | integration tests that hit OANDA | OANDA practice account (**never** live) |
| `OANDA_ACCOUNT_ID` | ditto | same OANDA practice account |

Deliberately not set (the cloud agent must not touch these):

| Env var | Why withheld |
|---|---|
| `RAILWAY_TOKEN` | deploys only run from `main` CI, never from an agent branch |
| `FERNET_KEY` | encrypts user credentials at rest; only the deployed app needs it |
| `SMTP_PASSWORD` / SendGrid key | notifications go via the deployed app, not the agent |
| Any `*_UAT` or `*_PRODUCTION` database URL | agent only runs the `test` profile |

### 1b. In GitHub (one click)

Repo → Settings → General → Pull Requests → turn on **Allow auto-merge**. Needed by `.github/workflows/agent-fleet-ci.yml` for the three-tier gate.

### 1c. In the Linear workspace

Nothing — the GitHub integration is already live, and `.mcp.json` points at `https://mcp.linear.app/mcp` which uses `LINEAR_API_KEY` from env.

## 2. What the cloud agent needs to be able to do, verified in-session

The agent should be able to run these from a fresh clone, all green:

```bash
# Env sanity
test -n "$ANTHROPIC_API_KEY" -a -n "$LINEAR_API_KEY" -a -n "$GITHUB_TOKEN" \
  -a -n "$OANDA_API_KEY" -a -n "$OANDA_ACCOUNT_ID" || echo "MISSING SECRET"

# Install
make dev                 # creates venv, installs dev deps + pre-commit

# Fast gate (no Docker)
make test-unit           # backend pytest (-m unit) + frontend Vitest

# Slow gate (needs Docker — most cloud providers give you docker-in-docker)
make docker-up PROFILE=test
make test-integration    # pytest -m "integration and not slow" against Postgres on :5433
make docker-down PROFILE=test
```

`make test-unit` alone is the pre-PR gate the operating model requires (per CLAUDE.md section 7 rule 7). `test-integration` is needed only when the change touches DB code.

## 3. First trial

Pick AFX-13's own definition of "nightly routine" as the first trial issue — small, bounded, lets you see the full workflow end-to-end from the phone:

1. Cloud agent follows AFX-13's **Self-delegation protocol**: picks the top Ready issue, sets delegate=self, state=In Progress.
2. Branches `agent/AFX-N-slug`, implements, commits, runs `make test-unit` locally.
3. Opens PR to `feat/agent-fleet` with `AFX-N` in the title → Linear auto-moves to **In Review** (verified: `gitAutomationStates` on team AFX wires `review → In Review`).
4. Wilson reviews on phone (Linear mobile app + GitHub mobile), approves.
5. If labelled `agent-authored`, `.github/workflows/agent-fleet-ci.yml` auto-merges on green CI → Linear auto-moves to **Done** (`merge → Done`).

## 4. Troubleshooting

- **Agent picks up a Money-path issue**: that's fine, but its PR will never auto-merge — the three-tier gate requires Wilson's manual approval on `main` PRs. The agent should add `needs-wilson` to the PR body so you see it on your phone.
- **`make test-integration` fails with "could not connect to server"**: Docker-in-Docker not enabled on the cloud agent provider. Fall back to the agent provider's hosted Postgres and set `DATABASE_URL` manually; the test profile uses port 5433 by default.
- **Linear MCP times out**: usually means `LINEAR_API_KEY` isn't set in the agent env. Check with `env | grep LINEAR_` (never log the value itself).
- **GitHub push rejected (403)**: `GITHUB_TOKEN` scope is wrong. Fine-grained tokens need `Contents: write` + `Pull requests: write` on this repo specifically.

## 5. Off-limits (same as every agent)

- `dev` / `uat` profiles → **test** profile only.
- Live OANDA keys → **practice** only.
- Pushing to `main` → always through a PR; `main` is Wilson's gate.
- `needs-wilson` issues → leave them even if Ready.
