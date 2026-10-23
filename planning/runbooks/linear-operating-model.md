# AgentFX on Linear — operating model

Approved by Wilson 2026-10-22. Workspace: https://linear.app/agentfx (team **AFX**).
The workspace structure is defined in `config/linear/workspace.yaml` and applied with `scripts/linear/bootstrap.py`. CLAUDE.md carries the short version of the agent rules (section 7).

## 1. Principles

1. **Wilson is the only human and the only one who approves work.** Agents can *propose* work. They can never approve it.
2. **Approval happens in two places only:** (a) a ticket leaving Triage, and (b) a pull request merging to `main` or touching the money path. Everything else runs without Wilson.
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

```
Triage ──Wilson accepts──▶ Backlog ──Wilson prioritises──▶ Ready ──agent picks up──▶ In Progress ──PR opened──▶ In Review ──PR merged──▶ Done
   │                                                                                                   │
   └──Wilson declines──▶ Canceled / Duplicate                                     (money-path: Wilson approves the PR in GitHub)
```

| State | Category | Who moves it here | Meaning |
|---|---|---|---|
| **Triage** | Triage | Agents (always), Wilson | Proposed, not approved. Agents file every new issue here. |
| **Backlog** | Backlog | Wilson only | Approved as worth doing, not yet scheduled. |
| **Ready** | Unstarted | Wilson only | Approved and prioritised. **This is the only state agents may pick work from.** |
| **In Progress** | Started | Coding agent | Work started on branch `agent/AFX-123-short-slug`. |
| **In Review** | Started | Automatic, via the GitHub integration when the PR opens | CI running; awaiting review agent or Wilson. |
| **Done** | Completed | Automatic on PR merge | Shipped. |
| **Canceled** / **Duplicate** | Canceled | Wilson; agents only for their own duplicates | |

Linear can't stop an agent from moving a ticket out of Triage. The rule is enforced in two ways: agent instructions forbid it, and a weekly audit (a ticket) flags any move into Backlog or Ready that Wilson didn't make.

## 4. Labels (each group allows one value per issue)

- **Type:** Bug · Feature · Improvement · Tech debt · Test gap · Chore · Research · Incident
- **Area:** Backtest · Evolution/DSL · Validator · Paper runner · OANDA/data · Agent fleet · Portal · Infra/CI · Notifications
- **Source:** Wilson · PM agent · QA agent · PE agent · Research agent · Coding agent · CI
- **Found in** (Bugs only): CI · Paper trading · UAT · Staging. Shows where defects escape to and therefore where tests are missing.
- **Risk:** Money-path (order execution, position sizing, kill switch, credentials, live/paper deploy gates) · Normal
- **Flags** (several allowed): `needs-wilson` (an agent is blocked on a decision) · `agent-filed`

## 5. Priority

- **Urgent:** only for Incidents. A running paper (later live) trade is halted, erroring or at risk. Also sends an email.
- **High / Medium / Low:** set by Wilson when moving to Ready. Agents may suggest a priority in the description but not set one.

## 6. Ticket templates

Every template has the same backbone so any agent can pick it up cold:

**Feature / Improvement:** Why (link to profit) · What · Done when (checkboxes, including test evidence) · Off-limits · Context (files, commits)

**Bug / Defect:** What happened · Expected · Evidence (logs, run IDs, screenshots) · Steps to reproduce · Found in · Done when (a test that reproduces the bug fails before the fix and passes after)

**Agent finding** (for QA/PE/PM filings): Fingerprint (a stable id such as `qa:pool-stuck-lease`) · Evidence · Proposed fix · Confidence (High/Med/Low) · Cost of ignoring it

**Test gap:** What isn't covered · How a bug could slip through · Proposed tests

## 7. Rules for agents (copied into CLAUDE.md)

Creating tickets:
1. **Search before creating.** If an open issue has the same fingerprint or clearly the same problem, add a comment with the new evidence instead of filing a new issue.
2. **Always file into Triage** with Type, Area and Source labels, the `agent-filed` flag, and the right template filled in.
3. **Cap:** at most 5 new issues per agent per day, so your phone isn't flooded.
4. **Never** set priority, move an issue out of Triage, or edit the scope of an approved ticket. Propose scope changes as a comment or a new Triage issue.

Working tickets:
5. **Pick only from Ready**, highest priority first. Make yourself the delegate (Wilson stays the assignee). Move the issue to In Progress.
6. **Branch** `agent/AFX-123-slug`. Put `AFX-123` in the PR title so Linear links the PR.
7. **Before opening the PR:** `make lint` and `make test-unit` green. Post a comment listing what was tested and the CI link.
8. **Money-path issues:** the PR is never auto-merged. Add `needs-wilson`.
9. **When blocked:** comment the question, add `needs-wilson`, and stop. Don't guess on product decisions.
10. All the existing repo rules still apply: test profile only, practice OANDA keys only, never push to `main`.

## 8. How testing is tracked

Linear is not a test-case manager. We don't need one.
- **Per ticket:** "Done when" includes test evidence. The agent's In Review comment lists what was tested. The linked PR shows CI status inside Linear.
- **Escapes:** Bugs carry a *Found in* label. A bug found in Paper/UAT/Staging rather than CI means a test was missing, so the coding agent files a *Test gap* issue alongside the fix.
- **Coverage drift:** the QA agent may file Test gap issues as findings.

## 9. Who does the work, in phases

| Phase | Picks up Ready work | Files Triage issues | Identity in Linear |
|---|---|---|---|
| **1 (now)** | Cloud Claude Code via the Linear MCP connector. Either a scheduled routine (e.g. nightly: "take the top Ready issue") or Wilson from the phone: "work on AFX-12". | Claude Code sessions | Acts as Wilson; distinguished by the *Source: Coding agent* label and signed comments |
| **2** | Same | In-platform fleet agents (QA/PE/PM) through a small `linear_client.py`. Replaces "email Wilson a proposal" with "file Triage issue + daily email digest". | **AgentFX Fleet** app user (Linear `actor=app`; free, not a seat) |
| **3 (optional)** | Delegating an issue to the AgentFX app in Linear triggers a cloud coding run via webhook | All agents | One app user per agent role |

Phase 2 also answers the PE Phase 2b question (ticket T6). PE files issues, and the same coding agent does the work.

## 10. Wilson's routine (on the phone)

- **Daily, ~5 min:** clear the Triage inbox (accept → Backlog/Ready, or decline), and answer anything tagged `needs-wilson`.
- **On notification:** approve money-path PRs in the GitHub app.
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
- Connect the GitHub integration (Settings → Integrations → GitHub), with PR opened → In Review and PR merged → Done.
- Install the mobile app and turn on notifications for Triage and "assigned to me".
- Revoke the `bootstrap` personal API key once structure changes settle. Re-create it when the YAML changes.

To change the workspace: edit `config/linear/workspace.yaml` in a PR, then run `python scripts/linear/bootstrap.py --dry-run` followed by `python scripts/linear/bootstrap.py`. The script never deletes anything.
