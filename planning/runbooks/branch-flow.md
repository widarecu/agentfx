# Branch flow: agent branches → feat/agent-fleet → main

```
agent/AFX-<n>-<slug>  --squash-->  feat/agent-fleet  --merge commit-->  main
                                          ^                              |
                                          +-- auto back-merge (bot) -----+
```

Only `main` deploys to staging (Railway backend, Vercel frontend). "Merged" is ambiguous on this project: always say "merged to `feat/agent-fleet`" or "merged to `main`".

## Rules

- Agent PRs target `feat/agent-fleet` and are **squash** merged. Never target `main`.
- Promotes from `feat/agent-fleet` to `main` are **merge commits**. Wilson approves every promote.
- Never cherry-pick onto a branch cut from `main`. `feat/agent-fleet` can lag `main` on direct-to-main hotfixes, so diff the dataclasses and signatures a commit touches before moving it between branches.
- Never squash a back-merge or a promote PR. Squashing breaks the shared history and the drift comes back.

## Automation

**Promote PR:** [agent-fleet-promote.yml](../../.github/workflows/agent-fleet-promote.yml) keeps one promote PR open (head `feat/agent-fleet`, labels `promote` and `needs-wilson`) whenever the fleet branch has content `main` lacks. The PR lists every unpromoted PR with its risk and files, Money-path first, and comments the link on each `AFX-<n>` issue. It runs on merges by a person or an App, and every 30 minutes for agent PRs, because auto-merges made with `GITHUB_TOKEN` don't start workflows. Don't open a second promote PR. To freeze a promote at a specific commit, cut a `promote/*` branch from `feat/agent-fleet`; the workflow then closes its live PR and won't open another until that one is merged or closed.

**Promoted-to-main comment:** every PR merged into `main` (promotes and hotfixes) triggers [agent-fleet-promoted-to-main.yml](../../.github/workflows/agent-fleet-promoted-to-main.yml) (AFX-68), the follow-up to agent-fleet-promote.yml's comment. It finds the `AFX-<n>` ids the merge carries (first-parent commits that just landed plus the PRs that landed them) and comments once per issue: promoted to `main` in PR #N at <time>, with the Railway staging deploy link. Re-runs are no-ops (per-PR marker). Linear failures are logged and never fail the job. Replay a PR with `workflow_dispatch` (`dry_run` defaults to true).

**Back-merge:** every push to `main` (promotes and hotfixes) triggers [main-to-fleet-backmerge.yml](../../.github/workflows/main-to-fleet-backmerge.yml). It opens a `main` → `feat/agent-fleet` PR as `agentfx-fleet-bot`, which auto-merges as a merge commit once the fast checks pass. On a conflict it adds `needs-wilson` and is never resolved automatically.

**Linear state:**
- Merge to `feat/agent-fleet` → **In Staging** (Linear's branch-specific PR automation).
- When the `Deploy` workflow finishes after a push to `main`, [agent-fleet-state-on-promote.yml](../../.github/workflows/agent-fleet-state-on-promote.yml) (AFX-70) reads the result. Success moves each `AFX-<n>` in the merge to **Done** with a staging-link comment. Failure moves them to **Blocked**, adds `needs-wilson` and posts a warning comment.
- Linear's own PR automation never sets Done.

## Checking what is unpromoted

`git log origin/main..origin/feat/agent-fleet` lists unpromoted commits. Whether a promote is actually needed depends on `git diff origin/main origin/feat/agent-fleet`, not on commit counts: back-merge commits appear in the log without changing any content.

## Migrations across branches

Before shipping an Alembic migration, check the migration history has a single head on the target branch **and** on `main`. With two long-lived branches it is easy to create two heads that only collide at promote time. See `.claude/rules/migrations.md`.
