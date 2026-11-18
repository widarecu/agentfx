---
name: work-issue
description: Pick up a Linear AFX issue and take it to an open PR into feat/agent-fleet. Use when asked to "work on AFX-<n>", "take the top Ready issue", or start any ticketed change.
---

# Work a Linear issue

Canonical rules: `planning/runbooks/linear-operating-model.md` §7. Use the `linear-server` MCP tools.

1. **Choose.** If no issue was named, list team `AFX` issues in state **Ready** and take the highest priority. Only work issues in Ready. If the named issue is not in Ready, stop and say so.
2. **Check it is still open in reality.** Grep the code for the feature before planning; shipped work with a stale ticket is common. If it is already done, comment the evidence and stop.
3. **Claim it.** Make yourself the delegate (Wilson stays assignee) and move it to **In Progress**.
4. **Branch** from the latest fleet branch:
   ```bash
   git fetch origin feat/agent-fleet
   git checkout -b agent/AFX-<n>-<slug> origin/feat/agent-fleet
   ```
5. **Build to the issue's "Done when"**, nothing more. Don't change approved scope; propose changes as a comment or a new Triage issue. If a product decision blocks you: comment the question, post `<!-- needs-input: wilson -->`, add `needs-wilson`, move to **Needs Input**, and stop.
6. **Before the PR**, run the `pr-preflight` skill.
7. **Open the PR** against `feat/agent-fleet`. Title includes `AFX-<n>` (only because this PR closes it). Reference other tickets in the body under "Related", never in the title.
   - Risk **Money-path** (order execution, sizing, kill switch, credentials, deploy gates): add `needs-wilson`; it must never auto-merge.
8. **Comment on the issue**: what was tested, and the CI link.

State after merge is automatic: merge to `feat/agent-fleet` → In Staging; promote to `main` plus a successful deploy → Done. Don't set these by hand.
