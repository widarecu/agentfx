# PE agent: Managed Agents setup

The Platform Engineer (PE) agent applies one fleet proposal per session, runs tests, and opens a PR into `main` ([delivery pipeline](delivery-pipeline.md)). It runs as a Claude Managed Agent. Its standing instructions live in the Anthropic Console, not in this repo, so this file keeps a copy under version control.

**When you change the agent in the Console, update this file in the same PR**, so the prompt the agent runs on can be reviewed like any other change.

The backend refers to the agent by ID (`AGENT_PE_WORKSPACE_AGENT_ID`, plus `..._ENVIRONMENT_ID` and `..._VAULT_ID`; see `.env.example`). Each dispatch adds a per-task kickoff message built by `_render_dispatch_narrative` in `src/agentfx/agents/fleet/platform_engineer.py` and `_build_managed_kickoff` in `src/agentfx/agents/fleet/pe_workspace.py`. The Devin backend uses `_build_devin_prompt` in the same file instead.

## Config (2026-11-19 Console copy, with the AFX-126 prompt changes Wilson applies in the Console)

- **name:** `agentfx-fleet-pe`
- **model:** `claude-opus-5-5`, effort `high`, speed `standard`
- **tools:** `agent_toolset_20260401` (permission `auto`), with `web_fetch` and `web_search` disabled
- **MCP servers:** `linear` (`https://mcp.linear.app/mcp`) and `github` (`https://api.githubcopilot.com/mcp/`) with every tool enabled, permission `auto`. `railway` (`https://mcp.railway.com/`) is read-only: default disabled, with only these nine tools enabled (permission `always_allow`): `get-logs`, `list-deployments`, `get-status`, `get-deployment-diagnosis`, `environment-status`, `describe-service`, `list-services`, `list-projects`, `get-service-metrics`. To give the agent another Railway tool, enable it by name; never switch the default back on
- **multi-agent:** advisor, subagents and workflows all disabled
- **skills:** none

```text
You are the AgentFX Platform Engineer workspace agent. Your job is to
apply a single proposal from the AgentFX fleet, pass tests, and open
a PR into `main`.

STEP 1 — Session bootstrap. On your FIRST turn of EVERY session, run
this bootstrap exactly once (idempotent — safe to re-run):

  echo "$GITHUB_TOKEN" | gh auth login --with-token
  gh auth setup-git
  git config --global user.email "<APP_USER_ID>+agentfx-pe[bot]@users.noreply.github.com"
  git config --global user.name  "agentfx-pe[bot]"
  mkdir -p /workspace && cd /workspace
  if [ ! -d agentfx ]; then
    git clone --depth 50 https://github.com/widarecu/agentfx.git
  fi
  cd agentfx
  git fetch --depth 50 origin main
  git checkout main
  git reset --hard origin/main
  pip install -q --upgrade pip
  pip install -q -r requirements-dev.txt

STEP 2 — Read the repo guidance. Read `CLAUDE.md`. Then, for every
path the kickoff message names, read each file the "Read before
changing" table in CLAUDE.md lists for that area. These are binding
in addition to the rules below.

HARD RULES:

1. NEVER push to `main`. Always branch off `main` using the
   `agent/<task-slug>` convention from the kickoff message's
   "Workflow" section, and open the PR into `main`.

2. Run `make lint` and `make test-unit` BEFORE pushing. If either
   still fails after 3 iterations, STOP and report the failing output
   in your final response — do NOT push a red branch.

3. After `gh pr create`, immediately run:
     gh pr edit <number> --add-label agent-authored
   `pr-policy` then auto-merges it once CI is green. Without the
   label the PR waits for a human and your work stalls.

4. Add `agent-authored` whatever the risk, Money-path included.
   `pr-policy` holds Money-path PRs until Wilson approves them.
   Use `needs-wilson` only when you cannot finish without a decision.

5. Keep scope tight — only touch the files the kickoff message
   names, plus their tests. No drive-by refactors.

6. cwd is /workspace/agentfx. Use the pre-installed `gh`, `git`,
   and `make`. `GITHUB_TOKEN` is injected from the vault (see "Where the GitHub token comes from").

7. The PR title and branch name exactly one `AFX-<n>`: the issue
   this PR closes. Linear also acts on IDs in the body after
   "Fixes/Closes/Resolves" (closes) or "Part of/Related to/Refs"
   (links), so mention other issues only without those words.

8. Linear: you may read any AFX issue for context. File follow-up
   issues only into Triage, following
   `.claude/skills/file-issue/SKILL.md`: search for duplicates first,
   at most 5 new issues per day. Never set priority, never move an
   issue out of Triage, never edit the scope of an approved issue.

9. Railway is READ-ONLY: logs and deployment status. Never deploy,
   redeploy, restart, or change variables, settings, domains or
   services, and never print variable values. Railway changes happen
   only through the deploy pipeline on `main`.
```

## Where the GitHub and Linear tokens come from (AFX-172, not yet live)

With `AGENT_PE_GITHUB_APP_CLIENT_ID` and `AGENT_PE_GITHUB_APP_PRIVATE_KEY` set on Railway, each dispatch does this, in order, immediately before `sessions.create`:

1. Mint **one** installation token for the `agentfx-pe` GitHub App (restricted to `widarecu/agentfx`; Contents write, Pull requests write, Metadata read; no Workflows).
2. Write that token into the vault's `GITHUB_TOKEN` environment-variable credential (`AGENT_PE_GITHUB_VAULT_CREDENTIAL_ID`) with `client.beta.vaults.credentials.update(credential_id, vault_id=..., auth={"type": "environment_variable", "secret_value": token})`. This is what `gh` uses, so PRs are authored by `agentfx-pe[bot]`.
3. Pass the same token as the mounted repository's `authorization_token`. This is what `git clone` and `git push` use.
4. If `AGENT_PE_LINEAR_VAULT_CREDENTIAL_ID` is set: write the "AgentFX Platform Engineer" Linear application's token (`linear_agent_api.get_app_token()`, about 30 days, cached) into that vault credential with `auth={"type": "static_bearer", "token": token}`, so the Linear connector acts as the app instead of as Wilson. If it is empty, the connector keeps whatever sign-in the vault holds (today Wilson's).

Refusals (the dispatch is marked failed with the reason; no session is created; the personal token is never used): the App is configured but `AGENT_PE_GITHUB_VAULT_CREDENTIAL_ID` is empty; only one of the two App settings is set; minting fails; either vault update fails; the Linear credential ID is set but the Linear token cannot be obtained. With neither App setting set, nothing changes: the personal `GITHUB_TOKEN` is used and no vault call is made.

**Overlap.** The vault credential is shared, so a later dispatch replaces the GitHub token seen by a session that is still running. That is safe because every token is valid for one hour from its own minting, and it is why the kickoff tells the agent to publish within about 40 minutes and to stop and report on an auth error.

**Lifetime.** An App token lasts one hour; a session can run up to 4 hours before the sweeper reclaims it. There is no refresh service.

**By design.** The App has no Workflows permission, so a PE change touching `.github/workflows/` cannot be pushed.

**Not verified without a live session:** that the Console accepts the `environment_variable` update on the installed SDK's endpoint (the SDK type hint only exists from anthropic 2.14.0; the code sends a plain dict), that the sandbox's `gh` picks up the updated value in a new session, and that the Linear connector accepts a static bearer credential in place of the sign-in.

## How a session's outcome is decided (AFX-182)

Every 10 minutes the backend reads each running session's status and its newest 100 events and decides the outcome from what Anthropic reports, not from text patterns or a timer. The last status event wins, so a session that went idle and was then sent another message is working again.

| What the session reports | Outcome | What the backend does |
|---|---|---|
| The session has settled (idle, or terminated) and GitHub has a pull request whose head is the run's branch (`agent_branch`) | PR opened | Row `pr_open` with GitHub's link, session archived |
| `session.status_idle`, stop reason `end_turn`, no PR | Completed without a PR | Row `completed_no_pr` (not a failure; `tests_failed` if the final message says tests still fail), summary starting `[session-outcome] completed_without_pr`; session archived; one comment on the ticket with the session's final message (trimmed, secrets removed); ticket stays In Progress with `needs-wilson` |
| `session.error` with retry status `terminal` or `exhausted`, or idle with `retries_exhausted` | Failed | Row `workspace_failed` with summary starting `[session-outcome] failed <error type>`; session archived; one comment with the reason in plain words; In Progress with `needs-wilson` |
| Idle with `requires_action` | Waiting | Row stays `running` (nothing surfaces it yet; AFX-183) |
| Running, rescheduled, an error still `retrying`, or anything the backend does not recognise | Still working | Row stays `running`; the 4-hour sweeper reclaims it if it never settles |
| Status `terminated` with no error | Ended without saying why | Classified from the final text, as before AFX-182 |

Whether a PR was opened is asked of GitHub (`GET /repos/widarecu/agentfx/pulls?head=widarecu:<agent_branch>&state=all`), only once the session has settled, never read from the session's text: a session that cites an old PR is still "completed without a PR". If GitHub cannot be asked (network error, non-200, no token), the run stays open and is retried next poll. If the agent pushed a branch other than `agent_branch`, the run closes as completed without a PR, with the final message on the ticket. The run closes on the first poll that sees a final outcome. No number of idle polls is counted. The "exceeded 4h" wording is only for the sweeper. A failed or no-PR session still counts against `AGENT_PE_WORKSPACE_MAX_PER_DAY`, because the cap counts every dispatched row.

**Billing pause.** After a session fails with `billing_error` (the Anthropic account is out of credit or at its spend limit), the scheduled scan and Linear delegation both refuse new Anthropic sessions (`anthropic_billing_paused` in the log and in Linear) until the next UTC midnight. A later session that succeeds (`pr_open`, `merged` or `completed_no_pr`) ends the pause early. Nothing is stored separately: the pause is read from the finished runs, so it survives a deploy. Devin dispatches are not held by it. To lift it by hand, top up the account and retry the ticket; the pause then ends at midnight UTC.

**Not verified without a live session:** the exact order and content of a real session's events (the code follows the installed SDK types, `anthropic` 0.100.0, and reads fields defensively), and that a message sent to an idle session emits `session.status_running`.

## Change log

**AFX-172 (the PE's own GitHub and Linear identities), for Wilson to apply in order after merge. Not yet live:**

| Step | Change | Why |
|---|---|---|
| a | Railway: set `AGENT_PE_GITHUB_VAULT_CREDENTIAL_ID` to the ID (`vcrd_...`) of the existing `GITHUB_TOKEN` credential in the vault `agentfx-fleet-pe-vault`. (`AGENT_PE_GITHUB_APP_CLIENT_ID` and `AGENT_PE_GITHUB_APP_PRIVATE_KEY` are already set.) | Without it, dispatch is refused: `gh` would keep using the personal token |
| b | Console prompt: bootstrap lines become `git config --global user.email "<APP_USER_ID>+agentfx-pe[bot]@users.noreply.github.com"` and `git config --global user.name "agentfx-pe[bot]"`. Replace `<APP_USER_ID>` with the `id` from `https://api.github.com/users/agentfx-pe%5Bbot%5D` | Commits are attributed to the App, not to Wilson's address |
| c | After one PE PR shows `agentfx-pe[bot]` as author: delete the personal token `agentfx-fleet-pe` in GitHub and remove `GITHUB_TOKEN` from Railway. The vault credential stays; the backend now owns its value | Removes the last personal token from the hosted PE |
| d | Linear (optional): in the Console vault, replace the OAuth credential for `https://mcp.linear.app/mcp` with a `static_bearer` credential (any placeholder value), then set `AGENT_PE_LINEAR_VAULT_CREDENTIAL_ID` on Railway to its ID | The PE's Linear writes are recorded as "AgentFX Platform Engineer", not Wilson |

**AFX-126 (single-branch cutover), for Wilson to apply in the Console before `feat/agent-fleet` is deleted:**

| Change | Why |
|---|---|
| Bootstrap fetches and checks out `main` | `feat/agent-fleet` is retired; once it is deleted the old checkout fails before the agent starts |
| Rule 1 and the intro: branch off and open PRs into `main` | Agent PRs target `main` directly ([delivery-pipeline.md](delivery-pipeline.md)) |
| Rules 3–4: `pr-policy` auto-merges; Money-path waits for Wilson's approval | The fleet CI auto-merge and the promote PR are gone |
| Rule 7: exactly one ID in title and branch; body magic words | `pr-policy` and deploy sync read the title and branch; Linear acts on magic words in the body |

**2026-11-19 (AFX-96, AFX-98), applied by Wilson:**

| Change | Why |
|---|---|
| New step 2: read `CLAUDE.md` and the files its "Read before changing" table lists for the paths in the kickoff | The Managed Agents sandbox does not load CLAUDE.md, rules or hooks automatically, so the agent missed rules such as `.env.example` parity, single Alembic head, and the portal UI test bar |
| Rule 2 runs `make lint` as well as `make test-unit` | Both are required before a PR (`CLAUDE.md` rule 8); lint failures used to surface only in CI |
| Rule 4 no longer adds `needs-wilson` to Money-path PRs | AFX-98: Money-path PRs auto-merge into `feat/agent-fleet` like any other; Wilson reviews them on the promote PR to `main` |
| New rule 7: ticket IDs in the PR title only if the PR closes that ticket | Linear closes every ID it finds in a merged PR title |
| Bootstrap logs in with `gh` before cloning, and clones with the plain URL | The old clone wrote the token into `.git/config` inside the sandbox. `gh auth setup-git` already provides git credentials |
| Step 2 no longer says the agent lacks Linear tools; new rule 8 | The PE agent reads related issues and files follow-ups into Triage under the same rules as other agents |
| New rule 9: Railway is read-only | Railway changes happen only through the deploy pipeline on `main` |
| Railway toolset: default disabled, nine read tools enabled by name | Enforces rule 9 in the config, so write tools (deploy, restart, delete, variables) are unavailable even if the prompt is ignored, and tools Railway adds later stay off |
| Advisor, subagents and workflows disabled | Keeps cost per dispatch down |
| Effort `high` instead of `max` | `max` is the most expensive setting and PE tasks are deliberately narrow (rule 5). Compare cost and pass rate over a week of dispatches; go back to `max` if hard tasks start needing retries |

Before applying, check whether the sandbox image has Docker. If it does not, `make test-unit` still works (unit tests need no database) but integration tests cannot run there.
