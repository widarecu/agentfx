---
name: work-issue
description: Pick up a Linear AFX issue and take it to an open PR into main. Use when asked to "work on AFX-<n>", "take the top Ready issue", or start any ticketed change.
---

# Work a Linear issue

Canonical flow: `planning/runbooks/delivery-pipeline.md`. Use the Linear MCP tools to read issues.

1. **Choose.** If no issue was named, list team `AFX` issues in state **Ready** and take the highest priority. Only work issues in Ready. If the named issue is not in Ready, stop and say so.
2. **Check it is still open in reality.** Grep the code for the feature before planning; shipped work with a stale ticket is common. If it is already done, comment the evidence and stop.
3. **Claim it:** `python3 scripts/afx.py start AFX-<n>` (needs `LINEAR_API_KEY`). It moves Ready → In Progress and refuses any other state; if it refuses, stop. Wilson stays assignee.
4. **Branch** from `main`:
   ```bash
   git fetch origin main
   git checkout -b agent/AFX-<n>-<slug> origin/main
   ```
5. **Build to the issue's "Done when"**, nothing more. Don't change approved scope; propose changes as a comment or a new Triage issue. If a product decision blocks you: `python3 scripts/afx.py block AFX-<n> "<question>"` (Blocked + `needs-wilson` + the question), and stop.
6. **Before the PR**, run the `pr-preflight` skill.
7. **Open one PR into `main`.** Its title and branch name exactly one AFX ticket: this one. Only mention another ticket in the body if needed, and never after "Fixes", "Closes", "Resolves", "Part of", "Related to" or "Refs": Linear acts on those.
   - Add the `agent-authored` label (`gh pr edit <number> --add-label agent-authored`) so `pr-policy` can auto-merge it. Money-path too: `pr-policy` holds it for Wilson's approval. Don't add `needs-wilson` for risk alone.
8. **Comment on the issue**: what was tested, and the CI link. Then **stop**.

Everything after is automatic: PR opened → In Review; merged to `main` → Deploy to staging; verified deploy → Done (deploy sync); failed deploy → Blocked + `needs-wilson`. Never set these by hand.
