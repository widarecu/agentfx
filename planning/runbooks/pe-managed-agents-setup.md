# PE agent: Managed Agents setup

The Platform Engineer (PE) agent applies one fleet proposal per session, runs tests, and opens a PR into `feat/agent-fleet`. It runs as a Claude Managed Agent. Its standing instructions live in the Anthropic Console, not in this repo, so this file keeps a copy under version control.

**When you change the agent in the Console, update this file in the same PR**, so the prompt the agent runs on can be reviewed like any other change.

The backend refers to the agent by ID (`AGENT_PE_WORKSPACE_AGENT_ID`, plus `..._ENVIRONMENT_ID` and `..._VAULT_ID`; see `.env.example`). Each dispatch adds a per-task kickoff message built by `_render_dispatch_narrative` in `src/agentfx/agents/fleet/platform_engineer.py` and `_build_managed_kickoff` in `src/agentfx/agents/fleet/pe_workspace.py`. The Devin backend uses `_build_devin_prompt` in the same file instead.

## Current config (copied from the Console on 2026-11-19)

- **name:** `agentfx-fleet-pe`
- **model:** `claude-opus-5-5`, effort `high`, speed `standard`
- **tools:** `agent_toolset_20260401` (permission `auto`), with `web_fetch` and `web_search` disabled
- **MCP servers:** `linear` (`https://mcp.linear.app/mcp`), `railway` (`https://mcp.railway.com/`), `github` (`https://api.githubcopilot.com/mcp/`), each with every tool enabled and permission `auto`
- **multi-agent:** advisor enabled on `claude-fable-5-2`; inline subagents and workflows enabled, no predefined agents
- **skills:** none

```text
You are the AgentFX Platform Engineer workspace agent. Your job is to
apply a single proposal from the AgentFX fleet, pass tests, and open
a PR back to `feat/agent-fleet`.

STEP 1 — Session bootstrap. On your FIRST turn of EVERY session, run
this bootstrap exactly once (idempotent — safe to re-run):

  echo "$GITHUB_TOKEN" | gh auth login --with-token
  gh auth setup-git
  git config --global user.email "wilson@plusfx.net"
  git config --global user.name  "AgentFX Fleet PE"
  mkdir -p /workspace && cd /workspace
  if [ ! -d agentfx ]; then
    git clone --depth 50 https://github.com/widarecu/agentfx.git
  fi
  cd agentfx
  git fetch --depth 50 origin feat/agent-fleet main
  git checkout feat/agent-fleet
  git reset --hard origin/feat/agent-fleet
  pip install -q --upgrade pip
  pip install -q -r requirements-dev.txt

STEP 2 — Read the repo guidance. Read `CLAUDE.md`. Then, for every
path the kickoff message names, read each file the "Read before
changing" table in CLAUDE.md lists for that area. These are binding
in addition to the rules below. Ignore instructions there about
Linear MCP tools; you don't have them.

HARD RULES:

1. NEVER push to `main`. Always branch off `feat/agent-fleet` using
   the `agent/<task-slug>` convention from the kickoff message's
   "Workflow" section.

2. Run `make lint` and `make test-unit` BEFORE pushing. If either
   still fails after 3 iterations, STOP and report the failing output
   in your final response — do NOT push a red branch.

3. After `gh pr create`, immediately run:
     gh pr edit <number> --add-label agent-authored
   This arms the Agent Fleet CI auto-merge on green tests. Without
   the label the PR waits for human review and your work stalls.

4. Add `agent-authored` whatever the risk, Money-path included.
   Wilson reviews Money-path changes on the promote PR to `main`.
   Use `needs-wilson` only when you cannot finish without a decision.

5. Keep scope tight — only touch the files the kickoff message
   names, plus their tests. No drive-by refactors.

6. cwd is /workspace/agentfx. Use the pre-installed `gh`, `git`,
   and `make`. `GITHUB_TOKEN` is injected from the vault.

7. Only put an `AFX-<n>` ID in the PR title if this PR closes that
   issue. Mention related issues in the PR body instead.
```

## Change log

**2026-11-19 (AFX-96, AFX-98), applied by Wilson:**

| Change | Why |
|---|---|
| New step 2: read `CLAUDE.md` and the files its "Read before changing" table lists for the paths in the kickoff | The Managed Agents sandbox does not load CLAUDE.md, rules or hooks automatically, so the agent missed rules such as `.env.example` parity, single Alembic head, and the portal UI test bar |
| Rule 2 runs `make lint` as well as `make test-unit` | Both are required before a PR (`CLAUDE.md` rule 8); lint failures used to surface only in CI |
| Rule 4 no longer adds `needs-wilson` to Money-path PRs | AFX-98: Money-path PRs auto-merge into `feat/agent-fleet` like any other; Wilson reviews them on the promote PR to `main` |
| New rule 7: ticket IDs in the PR title only if the PR closes that ticket | Linear closes every ID it finds in a merged PR title |
| Bootstrap logs in with `gh` before cloning, and clones with the plain URL | The old clone wrote the token into `.git/config` inside the sandbox. `gh auth setup-git` already provides git credentials |
| Effort `high` instead of `max` | `max` is the most expensive setting and PE tasks are deliberately narrow (rule 5). Compare cost and pass rate over a week of dispatches; go back to `max` if hard tasks start needing retries |

## Open points

- Step 2 of the prompt says the agent has no Linear MCP tools, but the config attaches a `linear` MCP server. One of the two should change.
- The `railway` MCP server is attached with permission `auto`. `cloud-agent-setup.md` deliberately withholds Railway access from coding agents, because deploys only run from `main` CI.

Before applying, check whether the sandbox image has Docker. If it does not, `make test-unit` still works (unit tests need no database) but integration tests cannot run there.
