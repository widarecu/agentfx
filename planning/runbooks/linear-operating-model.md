# AgentFX on Linear — operating model

Approved by Wilson 2026-10-22. Workspace: https://linear.app/agentfx (team **AFX**).
The workspace structure is defined in `config/linear/workspace.yaml` and applied with `scripts/linear/bootstrap.py`. CLAUDE.md carries the two headline rules (Ready only, Triage only); the `work-issue` and `file-issue` skills in `.claude/skills/` carry the procedure from section 7.

## 1. Principles

1. **Wilson is the only human and the only one who approves work.** Agents can *propose* work. They can never approve it.
2. **Approval happens in two places only:** (a) a ticket leaving Triage, and (b) a pull request into `main` that is Money-path or touches a code-owned path. Everything else runs without Wilson.
3. **Linear is the single source of truth for work items.** ROADMAP.md stays as the narrative. BACKLOG.md becomes a pointer to Linear.
4. **The setup is code.** States, labels, templates and projects are defined in `config/linear/workspace.yaml` and applied by `scripts/linear/bootstrap.py`. The script can be re-run safely, so changes are reviewed like any other PR and the workspace can be rebuilt.
5. **Every agent action is attributable.** Each agent identifies itself with a Source label and signs its comments.

## 2. Structure

| Linear concept | AgentFX use |
|---|---|
| Workspace | `AgentFX` |
| Team | One team, `AgentFX`, key **AFX** (issues are AFX-1, AFX-2…). A second team only if a separate product appears. The free plan allows 2. |
| Projects | One per roadmap release: *v0.8.2 Autonomous paper lifecycle*, *v0.9.0 Live-nomination pipeline*, *v1.0.0 Live trading*. Plus a standing *Platform health* project for defects not tied to a release. |
| Cycles (sprints) | **Off.** Agents don't need sprints. Turn them on later only if you want weekly throughput numbers. |
| Estimates | **Off** to start. |

## 3. Workflow states (the approval gate)

The full delivery flow, every status owner, review, CI, retries and the daily reconcile are in **[delivery-pipeline.md](delivery-pipeline.md)** (canonical). In short:

```
Triage ──Wilson accepts──▶ Backlog ──Wilson prioritises──▶ Ready ──afx start──▶ In Progress ──PR opened──▶ In Review ──merged to main + verified staging deploy──▶ Done
   │                                                                                 │                          └──failed deploy──▶ Blocked + needs-wilson
   └──Wilson declines──▶ Canceled / Duplicate                                         └──afx block──▶ Blocked + needs-wilson
```

| State | Category | Who moves it here | Meaning |
|---|---|---|---|
| **Triage** | Triage | Anyone | Proposed, not approved. Agents file every new issue here. |
| **Backlog** | Backlog | Wilson only | Approved as worth doing, not yet scheduled. |
| **Ready** | Unstarted | Wilson only | Approved and prioritised. **This is the only state agents may pick work from.** |
| **In Progress** | Started | `afx start AFX-n` (the one shared claim) | Work started on branch `agent/AFX-123-short-slug` off `main`; one comment names the taker. |
| **In Review** | Started | Linear GitHub integration, when the PR opens | CI and `pr-policy` running; Money-path waits for Wilson's approval. |
| **Blocked** | Started | `afx block AFX-n`, or deploy sync after a failed staging deploy (both add `needs-wilson`) | Waiting on Wilson. |
| **Done** | Completed | Deploy sync only, after the merge to `main` is verified running on staging | Shipped to staging. |
| **Canceled** / **Duplicate** | Canceled | Wilson; agents only for their own duplicates | |

**In Staging** and **Needs Input** were retired at the AFX-125 cutover and are archived by Wilson (see `delivery-cutover-checklist.md`). Never move a ticket into them.

Linear can't stop an agent from moving a ticket out of Triage. The rule is enforced in two ways: agent instructions forbid it, and the daily delivery reconcile flags any move into Backlog or Ready that Wilson didn't make.

## 4. Labels (each group allows one value per issue)

- **Type:** Bug · Feature · Improvement · Tech debt · Test gap · Chore · Research · Incident
- **Area:** Backtest · Evolution/DSL · Validator · Paper runner · OANDA/data · Agent fleet · Portal · Infra/CI · Notifications
- **Source:** Wilson · PM agent · QA agent · PE agent · Research agent · Coding agent · CI
- **Found in** (Bugs only): CI · Paper trading · UAT · Staging. Shows where defects escape to and therefore where tests are missing.
- **Risk:** Money-path (order execution, position sizing, kill switch, credentials, live/paper deploy gates) · Normal
- **Flags** (several allowed): `needs-wilson` (an agent is blocked on a decision) · `agent-filed`

## 5. Priority

Agents write a **Suggested priority** line in the description with a one-sentence reason. Only Wilson sets the actual priority field.

| Level | When |
| --- | --- |
| Urgent | A running paper or live trade is halted or at risk, credentials are exposed, or deploy is broken |
| High | Blocks a profit-chain stage or the current release; Money-path correctness bug |
| Medium | Improves pipeline throughput or quality; bug with a workaround; Money-path test gap |
| Low | Tech debt, chores, cosmetic work, speculative research |

## 6. Ticket templates

The Markdown files in [`config/linear/templates/`](../../config/linear/templates/) are the source for the Linear templates:

- [Feature / Improvement](../../config/linear/templates/feature.md)
- [Bug / Defect](../../config/linear/templates/bug.md) keeps expected versus observed behaviour, reproduction steps, Found in, and a regression test that fails before the fix and passes after.
- [Agent finding](../../config/linear/templates/finding.md) keeps a stable fingerprint, confidence, evidence, and the cost of ignoring the finding.
- [Test gap](../../config/linear/templates/test-gap.md) explains the missing coverage and how a bug could slip through.

All four connect **Why** to a profit-chain stage, describe **What** and **Out of scope** in plain language, attach evidence, and give **Done when** criteria in Given / When / Then form with a test level and command. Mark up to three open questions with `[NEEDS CLARIFICATION]`; any such question requires the `needs-wilson` flag.

Use the collapsed **Agent notes** for confirmed files, current behaviour, approach, dependencies, and size. The exclusive **Size** group has S, M, and L; the label matches the notes. Split L work before filing. Before saving, make sure an agent starting cold can complete the issue in one PR, duplicates were searched, and evidence is attached.

## 7. Rules for agents (canonical; summarised in CLAUDE.md and the `.claude/skills/` files)

Creating tickets:
1. **Search before creating.** If an open issue has the same fingerprint or clearly the same problem, add a comment with the new evidence instead of filing a new issue.
2. **Always file into Triage** with Type, Area and Source labels, the `agent-filed` flag, and the right template filled in.
3. **Cap:** at most 5 new issues per agent per day, so your phone isn't flooded.
4. **Never** set priority, move an issue out of Triage, or edit the scope of an approved ticket. Propose scope changes as a comment or a new Triage issue.

Working tickets (step by step in `delivery-pipeline.md` §1):
5. **Pick only from Ready**, highest priority first, and claim with `python3 scripts/afx.py start AFX-<n> --taker "<who>" --link <session or branch>` (Ready → In Progress for exactly one taker; everyone else and every other state is refused). It works with or without `LINEAR_API_KEY`. Wilson stays the assignee and delegate is never set; never claim by editing the ticket yourself.
6. **Branch** `agent/AFX-123-slug` off `main`. Open one PR into `main` whose title and branch name exactly one AFX ticket, the one it closes. Linear also reads the PR body: closing words ("Fixes/Closes/Resolves AFX-n") close that ticket and linking words ("Part of/Related to/Refs AFX-n") link it, so mention other tickets only when needed, without those words.
7. **Before opening the PR:** `make lint` and `make test-unit` green. Label the PR `agent-authored`, post a comment listing what was tested and the CI link, then stop. Merge, deploy and Done are automatic.
8. **Money-path issues:** label the PR `agent-authored` like any other. `pr-policy` holds it until Wilson approves its latest commit, and any PR touching a path in `.github/CODEOWNERS` needs his code-owner review.
9. **When blocked:** `python3 scripts/afx.py block AFX-<n> "question"` (Blocked + `needs-wilson` + the question), and stop. Don't guess on product decisions.
10. All the existing repo rules still apply: test profile only, practice OANDA keys only, never push to `main`.

## 8. How testing is tracked

Linear is not a test-case manager. We don't need one.
- **Per ticket:** "Done when" includes test evidence. The agent's In Review comment lists what was tested. The linked PR shows CI status inside Linear.
- **Escapes:** Bugs carry a *Found in* label. A bug found in Paper/UAT/Staging rather than CI means a test was missing, so the coding agent files a *Test gap* issue alongside the fix.
- **Coverage drift:** the QA agent may file Test gap issues as findings.

## 9. Who does the work, in phases

| Phase | Picks up Ready work | Files Triage issues |
|---|---|---|
| **1 (now)** | Cloud Claude Code via the Linear MCP connector. Either a scheduled routine (e.g. nightly: "take the top Ready issue") or Wilson from the phone: "work on AFX-12". | Claude Code sessions |
| **2** | Same | In-platform fleet agents (QA/PE/PM) through a small `linear_client.py`. Replaces "email Wilson a proposal" with "file Triage issue + daily email digest". |
| **3 (optional)** | Delegating an issue to the AgentFX app in Linear triggers a cloud coding run via webhook | All agents |

Phase 2 also answers the PE Phase 2b question (ticket T6). PE files issues, and the same coding agent does the work.

**Which identity each actor uses in Linear and GitHub** (today and the target) is in [identities.md](identities.md), not here.

## 10. Wilson's routine (on the phone)

- **Daily, ~5 min:** clear the Triage inbox (accept → Backlog/Ready, or decline), and answer anything tagged `needs-wilson`.
- **On notification:** review PRs into `main` that need you (Money-path tickets and code-owned paths) and approve their latest commit; `pr-policy` then merges them.
- **Daily:** read the delivery reconcile digest.
- **Weekly, ~15 min:** reorder Ready, and glance at the *Found in* counts.

## 11. Costs

- Free plan: 2 teams, 250 issues, API + agents included. Agents are not billable seats.
- Expect to outgrow 250 issues within months once agents file findings. Basic is $10/month for one user.

## 12. Setup status (2026-10-22)

Done:
- Workspace `AgentFX`, team `AFX`; states, labels, projects and templates applied by `scripts/linear/bootstrap.py`.
- Open BACKLOG.md items imported to **Backlog**. New work drafted by Claude is in **Triage** for Wilson.
- Linear MCP server: user-level config plus project `.mcp.json`, so cloud sessions get it too.

Wilson, still to do:
- Connect the GitHub integration (Settings → Integrations → GitHub), with PR opened → In Review and no merge-driven status change. Done is set only by deploy sync after a verified staging deploy (see `delivery-pipeline.md` and `delivery-cutover-checklist.md`).
- Install the mobile app and turn on notifications for Triage and "assigned to me".
- Revoke the `bootstrap` personal API key once structure changes settle. Re-create it when the YAML changes.

To change the workspace: edit `config/linear/workspace.yaml` in a PR, then run `python scripts/linear/bootstrap.py --dry-run` followed by `python scripts/linear/bootstrap.py`. The script never deletes anything.
