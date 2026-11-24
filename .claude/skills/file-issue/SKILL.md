---
name: file-issue
description: File a bug, finding, feature idea or test gap into Linear Triage with the right template and labels. Use whenever you find a problem or idea outside the current task's scope.
---

# File a Linear issue

Canonical rules: `planning/runbooks/linear-operating-model.md` §6–7; delivery flow: `planning/runbooks/delivery-pipeline.md`.

1. **Search first** (`list_issues`, team `AFX`, by keywords and by `Fingerprint:` if you have one). If an open issue covers it, add a comment with the new evidence instead.
2. **Cap:** at most 5 new issues per agent per day.
3. **Create in Triage** with the matching template (Feature, Bug, Agent finding, Test gap) and exactly one label from each group:
   - **Type**: Bug, Feature, Improvement, Chore, Tech debt, Test gap, Research, Incident
   - **Area**: Agent fleet, Backtest, Evolution/DSL, Infra/CI, Notifications, OANDA/data, Paper runner, Portal, Validator
   - **Source**: Coding agent (for Claude Code sessions), PE agent, QA agent, PM agent, Research agent, CI pipeline
   - **Risk**: Normal or Money-path
   - plus the `agent-filed` flag. Bugs also get one **Found in** label: CI, Staging, UAT, Paper trading.
   These groups are single-select: adding a second label from the same group fails.
4. **Write for Wilson.** Short sentences. Start with why it matters for profit. Put implementation detail in a collapsible section at the end:
   ```
   +++ Agent notes
   ...
   +++
   ```
5. **Never** set priority, move the issue out of Triage, or edit the scope of an approved issue.
6. **Don't fix it in the current PR.** A filed issue is worked only once Wilson moves it to Ready, through its own `afx start` and its own PR into `main`.
