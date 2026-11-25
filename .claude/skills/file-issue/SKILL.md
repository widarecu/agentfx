---
name: file-issue
description: File a bug, finding, feature idea or test gap into Linear Triage with the right template and labels. Use whenever you find a problem or idea outside the current task's scope.
---

# File a Linear issue

Canonical rules: `planning/runbooks/linear-operating-model.md` §6–7; delivery flow: `planning/runbooks/delivery-pipeline.md`.

1. **Search first** (`list_issues`, team `AFX`, by keywords and by `Fingerprint:` if you have one). If an open issue covers it, add a comment with the new evidence instead.
2. **Cap:** at most 5 new issues per agent per day.
3. **Use the checked-in template**: [Feature / Improvement](../../../config/linear/templates/feature.md), [Bug / Defect](../../../config/linear/templates/bug.md), [Agent finding](../../../config/linear/templates/finding.md), or [Test gap](../../../config/linear/templates/test-gap.md).
   Write for Wilson in short, plain sentences. Split **L** work before filing.
4. **Suggest a priority** in the description with a one-sentence reason. Wilson alone sets the priority field.

   | Level | When |
   | --- | --- |
   | Urgent | A running paper or live trade is halted or at risk, credentials are exposed, or deploy is broken |
   | High | Blocks a profit-chain stage or the current release; Money-path correctness bug |
   | Medium | Improves pipeline throughput or quality; bug with a workaround; Money-path test gap |
   | Low | Tech debt, chores, cosmetic work, speculative research |

5. **Ready check before saving**:
   - [ ] An agent with no session context could finish this in one PR.
   - [ ] You searched for duplicates.
   - [ ] Evidence is attached (e.g. logs, IDs, screenshots, or links).
6. **Create in Triage** with exactly one label from each applicable group:
   - **Type**: Bug, Feature, Improvement, Chore, Tech debt, Test gap, Research, Incident
   - **Area**: Agent fleet, Backtest, Evolution/DSL, Infra/CI, Notifications, OANDA/data, Paper runner, Portal, Validator
   - **Source**: Coding agent (for coding sessions), PE agent, QA agent, PM agent, Research agent, CI pipeline
   - **Risk**: Normal or Money-path
   - **Size**: S or M (matching Agent notes; split L before filing)
   - **Found in** (bugs only): CI, Staging, UAT, Paper trading
   - Add `agent-filed` and, if any question is marked `[NEEDS CLARIFICATION]`, `needs-wilson`.
7. **Never** set priority, move the issue out of Triage, or edit the scope of an approved issue.
8. **Don't fix it in the current PR.** A filed issue is worked only once Wilson moves it to Ready, through its own `afx start` and its own PR into `main`.
