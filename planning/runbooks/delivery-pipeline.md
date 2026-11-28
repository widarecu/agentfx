# Delivery pipeline: one branch, one owner per status

The canonical guide to how a change gets from a Linear ticket to staging (AFX-117, approved Option A: every merge to `main` deploys to staging). CLAUDE.md, the `.claude/skills/` files, the PE prompts and the setup runbooks summarise this file; if they disagree, this file wins and the others are a bug.

```
Ready ──afx start──▶ In Progress ──PR opens──▶ In Review ──squash merge to main──▶ Deploy (staging) ──verified──▶ Done
                          │                        │                                    └──failed──▶ Blocked + needs-wilson
                          └──afx block─────────────┴──▶ Blocked + needs-wilson
```

Only `main` exists. Every merge to `main` deploys to staging (Railway backend, Vercel frontend), which runs paper trading. Nobody pushes to `main` directly; every change reaches it through a PR.

## 1. The agent's steps (cold start)

1. **Pick** a ticket in **Ready** (only Ready; highest priority first). Grep the code first: shipped work with a stale ticket is common.
2. **Claim** it: `python3 scripts/afx.py start AFX-<n> --taker "<who you are>" --link <session URL or branch>`. It moves Ready → In Progress for exactly one taker and refuses everyone else (see [How the claim works](#how-the-claim-works)). If it refuses, stop. Never claim by editing the ticket in Linear yourself.
3. **Branch** from `main`:
   ```bash
   git fetch origin main
   git checkout -b agent/AFX-<n>-<slug> origin/main
   ```
4. **Build** to the ticket's "Done when", nothing more. Run `make lint` and `make test-unit` (skill `pr-preflight`).
5. **Open one PR into `main`** whose title and branch name exactly one AFX ticket: the one it closes. Add the `agent-authored` label: `gh pr edit <number> --add-label agent-authored`.
6. **Comment on the ticket** what was tested and the CI link. Then **stop**. Merge, deploy and Done are automatic; never set In Review, Done or any later state by hand.

**Blocked on a decision?** `python3 scripts/afx.py block AFX-<n> "<the question>"`. It moves the ticket to Blocked, adds `needs-wilson` and posts the question once, however often it is retried. Then stop; don't guess.

### How the claim works

Every taker uses the same `afx start` / `afx block` (AFX-142): Wilson with Claude Code on his laptop, standalone Claude or Devin sessions, the Devin Product Engineer bot's sessions, and the hosted fleet PE.

- **One winner.** Linear can't do "change the status only if it is still Ready", so the claim is decided on GitHub: the winner is whoever first creates the tag `afx-claim/AFX-<n>/<when the ticket entered Ready>`. GitHub refuses a second create of the same tag, so of two takers racing for one ticket exactly one starts; the other is told who has it and stops. A ticket put back into Ready gets a fresh tag.
- **What Linear shows.** Status is the only claim: Ready → In Progress, plus one comment "Claimed by <taker>: <session or branch>". Assignee stays Wilson and delegate is never set (delegating can itself launch a Linear agent session).
- **With or without a Linear key.** With `LINEAR_API_KEY` the command does it all in place (it also needs `gh` or `GH_TOKEN` for the tag). Without it (Devin and other keyless sessions) it pushes an `afx-request/<id>` branch; the `afx-request.yml` workflow runs the same code from `main` with the repo's credentials (the workflows authenticate as the AgentFX Pipeline app user), reports the verdict on that commit, deletes the branch, and the command prints the verdict. It needs only `git push` plus a GitHub token to read the verdict (`GH_TOKEN`, which Devin sessions have). The script needs Python 3.11+: in a Devin VM run it as `.venv/bin/python scripts/afx.py ...`, since the system `python3` there is 3.10.
- **The fleet** claims with the same function before it launches a worker and tells the worker not to claim again. It doesn't use an empty assignee as its pickup gate. Delegating a ticket to the PE in Linear claims it the same way before the session starts; if the claim is refused no session starts and the Linear agent session gets one error saying why, and if the dispatch then fails the ticket goes back to Ready. The fleet's claim signs in to GitHub as the `agentfx-pe` App; if the App is half set up or its token can't be minted, the claim fails and nothing is dispatched (it never falls back to `GITHUB_TOKEN`).
- **Retries.** Only the taker that created the tag is told to start; any later `afx start` on the ticket is refused, even from a session with the same name. If a claim's outcome is unknown, check the ticket's claim comment in Linear instead of retrying. Re-running `afx block` with the same question posts nothing new.

## 2. Who owns each status

| Status | Owner | How it gets there |
|---|---|---|
| Triage | Anyone | New issues are filed here (skill `file-issue`) |
| Backlog, Ready, Canceled | Wilson | By hand |
| In Progress | `afx start AFX-n` (the shared claim) | Any taker (laptop, agent session, the PE) claims a Ready ticket |
| In Review | Linear GitHub integration | A PR naming the ticket opens |
| Blocked + `needs-wilson` | `afx block`, or deploy sync after a failed staging deploy | Waiting on Wilson |
| Done | Deploy sync only | The change is verified running on staging, not just merged |

**In Staging** and **Needs Input** were retired at the AFX-125 cutover and are archived by Wilson ([cutover checklist](delivery-cutover-checklist.md) step 3). They are no longer in `config/linear/workspace.yaml`. Never move a ticket into either.

## 3. PR rules

- **Exactly one AFX ID in the title and branch.** `pr-policy` fails a PR that names none or several, and deploy sync can only close a ticket the title or branch names.
- **Other tickets:** Linear reads the PR body too. Closing words ("Fixes", "Closes", "Resolves") before an ID close that ticket on merge, and linking words ("Part of", "Related to", "Refs") link the PR to it, so the PR-opened automation can move it to In Review. Mention another ticket only when the reader needs it, without those words in front of the ID, and check afterwards that it didn't move.
- **Squash merge only**; the squash title is the PR title, so the ticket ID lands on `main`.
- **`agent-authored`** is what lets `pr-policy` auto-merge. Leave it off a handoff PR (`[NEEDS DESIGN]`, `[NEEDS WILSON]`): those merge only by hand.

## 4. Review: normal and Money-path

`pr-policy` runs on every PR into `main` and checks:
1. the PR names exactly one AFX ticket, in **In Progress** or **In Review**;
2. `CI required` passed on the PR's latest commit;
3. if the ticket has **Risk: Money-path** (order execution, sizing, kill switch, credentials, deploy gates), Wilson has approved the PR's latest commit.

When all pass on an `agent-authored` PR, `pr-policy` turns on auto-merge (squash) pinned to that commit. A new push re-runs everything.

- **Normal risk:** auto-merges on green, no human review, unless the diff touches a code-owned path.
- **Money-path code:** `.github/CODEOWNERS` lists the files that implement order execution, sizing, kill switch, credentials and deploy gates (plus `.github/` and the delivery scripts). Any PR touching them needs Wilson's code-owner review, whatever the ticket's Risk label.
- A Money-path PR authored by the approver (Wilson's laptop PRs) can't be approved, since GitHub bars self-approval. `pr-policy` fails it saying so, and Wilson merges it by hand. Only a person's review counts, never a GitHub App's.
- Don't add `needs-wilson` for risk alone; it is for real blockers.
- A review triggers the "PR review relay" workflow, which re-runs `pr-policy`, so Wilson's approval takes effect without a new push. The `main` ruleset drops an approval on every new push, so he approves again after any later commit.

## 5. CI

`ci.yml` is the only PR gate. It runs Backend (unit + integration), Frontend, E2E (with the disposable `test` profile settings, never `dev` or `uat`) and the advisory Settings UI testing bar on every PR into `main`, docs-only included. `CI required` fails if any of them fails.

The OANDA practice key never reaches unreviewed PR code: on PRs, the tests that call OANDA's real API skip themselves. The same suite runs with the key on the `CI` push run after each merge to `main`.

**CI fails on your PR:** fix it on the same branch and push. Don't open a second PR. After three honest attempts, `afx block` with the failing output.

## 6. Deploy and Done

A merge to `main` starts `deploy.yml`: plan, Railway + Vercel deploys, staging verification, then the `deployed/staging` tag moves to the verified commit. Its "Sync tickets" job (deploy sync, `scripts/afx.py deploy-sync`) then reads every PR merged since the previous successful `deployed/staging` commit:

Railway's service settings for the backend (Dockerfile builder, `/health` check, restart policy) live in `.railway/railway.ts`. The backend deploy job plans that file against staging and applies any difference before `railway up`; a failed plan or apply fails the backend deploy like any other failure. Change those settings in the file through a PR, never in the Railway dashboard.

- **Verified deploy:** each PR's ticket → **Done**, with a staging link comment.
- **Failed deploy:** each ticket → **Blocked** + `needs-wilson`, with the run link. The range isn't consumed: the next successful deploy covers it again and moves those tickets to Done.
- Deploy sync writes only when the repo variable `AFX_DEPLOY_SYNC_MODE` is `apply`; otherwise it only logs what it would do.

**Failed because of the change itself:** a re-run fails the same way, so the ticket needs a fix PR. Wilson removes `needs-wilson` and moves it from Blocked to Ready, which makes it an ordinary Ready ticket: agents and the PE leave `needs-wilson` tickets alone, so the label has to go for anyone to pick it up. An agent claims it with `afx start` (Ready → In Progress) and opens a fix PR that names the same ticket. When that fix's deploy is verified, deploy sync covers the failed commit and the fix together and moves the ticket to Done.

**Retries:** re-run the failed Deploy jobs, or run "Deploy" by hand (`workflow_dispatch`) with the commit's `sha`. Never fix ticket states by hand. A sync that was missed after a deploy is replayed with `python3 scripts/afx.py deploy-sync recover --mode apply` (it needs the `deploy-sync/baseline/<sha>` record the deploy wrote).

## 7. Daily reconciliation

`delivery-reconcile.yml` runs daily at 08:17 Sydney. It replays a missed deploy sync for the current `deployed/staging` (in `apply` mode only), then publishes one drift digest on the run summary without moving any ticket: In Progress for more than 3 days with no PR, In Review with no open PR, Done without a deploy-sync marker, Backlog/Ready moves not made by Wilson (matched by Linear user ID), PRs merged into `main` by anyone other than `agentfx-fleet-bot`, and anything it couldn't check. Wilson acts on the digest.

## 8. Wilson's part

- Moves tickets out of Triage, into Ready, and to Canceled.
- Answers `needs-wilson` tickets.
- Reviews PRs that need his code-owner review or a Money-path approval.
- Owns the GitHub ruleset, the Linear integration settings, `AFX_DEPLOY_SYNC_MODE` and the hosted PE prompt ([cutover checklist](delivery-cutover-checklist.md)).

## Migrations

Only one branch receives migrations, so keep a single Alembic head on `origin/main` (`.claude/rules/migrations.md`).
