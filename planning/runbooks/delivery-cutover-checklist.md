# Delivery cutover checklist (AFX-117)

Wilson's manual Linear and GitHub settings for the move to one branch (`main`) with one owner per ticket status. **Do these on cutover day with AFX-125, in the order below.** The AFX-125 PR removes the fleet pipeline (fleet fast gate, promote, back-merge and the legacy Done writer); the settings below must be in place before it merges.

## Who owns each status after cutover

| Status | Owner | How it gets there |
|---|---|---|
| Triage | Anyone | New issues are filed here |
| Backlog, Ready, Canceled | Wilson | By hand |
| In Progress | `afx start AFX-n` | Agent or PE claims a Ready ticket |
| In Review | Linear GitHub integration | A PR naming the ticket opens |
| Blocked + `needs-wilson` | `afx block AFX-n "question"`, or deploy sync after a failed staging deploy | Waiting on Wilson |
| Done | Deploy sync only | The change is verified running on staging (paper), not just merged |

`config/linear/workspace.yaml` holds these descriptions. `scripts/linear/bootstrap.py` only creates and updates states; it never archives or deletes one, so tickets on retiring states are safe.

## Before cutover day (no settings change)

- [ ] AFX-118 (`afx start/block`), AFX-119 (full CI on PRs into `main`), AFX-121 (verified deploy), AFX-122 (deploy sync) and AFX-120 (PR policy check) are merged.
- [ ] Run `python scripts/linear/bootstrap.py --dry-run`, then without `--dry-run`. Expected: state descri## Cutover day (with AFX-125)

The AFX-125 PR is the first PR into `main` under the new flow. `feat/agent-fleet` already matches `main` (the last promote, #174, deployed green), so there is nothing left to promote.

### 1. Before merging the AFX-125 PR

**GitHub: Settings → Rules → Rulesets, ruleset on `main`**

- [ ] Require a pull request before merging, and turn on "Require review from Code Owners" (off on `main` today; it was only on the `fleet-fast-gate` ruleset).
- [ ] Required status checks: `CI required` (from `ci.yml`) and `pr-policy`.
- [ ] Allowed merge method: **squash only**. Replace the `main-merge-commits-only` rule; promote and back-merge commits no longer exist.
- [ ] Settings → General → Pull Requests: squash commit title = **Pull request title**, so the ticket ID in the title lands on `main`.

**Linear: Settings → Integrations → GitHub**

- [ ] **Keep** "PR opened → In Review".
- [ ] **Turn off** every merge-driven status change ("PR merged → In Staging", and any "merged → Done"). Deploy sync sets Done after a verified staging deploy.
- [ ] Screenshot the settings page after the change and attach it to AFX-125.

**GitHub: Settings → Secrets and variables → Actions → Variables**

- [ ] Set `AFX_DEPLOY_SYNC_MODE` = `apply`. From the next deploy, deploy sync is the only Done/Blocked writer. Merging the PR deletes the legacy writer, so without this nothing moves tickets to Done.

### 2. Merge and watch the first deploy

- [ ] Wilson approves the AFX-125 PR's latest commit; `pr-policy` then lets it squash-merge.
- [ ] The Deploy run on `main` is green, and its "Sync tickets" job moves AFX-125 to Done (not dry-run).
- [ ] No "Transition Linear tickets on main-merge + deploy outcome", "Promote" or "Back-merge" run starts.

### 3. Retire the transitional states

Do these after merge-driven automation is off, so nothing moves tickets back onto these states.

- [ ] Move each remaining **In Staging** ticket by hand: to Done if its change is verified running on staging, otherwise to In Review or Blocked.
- [ ] Move each remaining **Needs Input** ticket to Blocked and add `needs-wilson`.
- [ ] When both states are empty, archive them in Linear (Settings → Teams → AFX → Workflow). Then delete their entries from `config/linear/workspace.yaml` in a PR.

### 4. Delete `feat/agent-fleet` (only after Wilson's explicit go)

- [ ] Every open PR into `feat/agent-fleet` is retargeted to `main` (`gh pr edit <n> --base main`) or closed. Retargeting does not start CI, so close and reopen each retargeted PR (`gh pr close <n> && gh pr reopen <n>`) to run `CI required`.
- [ ] The PE Managed Agents system prompt (`planning/runbooks/pe-managed-agents-setup.md`, set in the Anthropic console) fetches and checks out `main`, not `feat/agent-fleet`. Otherwise every hosted PE session fails its bootstrap once the branch is gone.
- [ ] `git diff origin/main origin/feat/agent-fleet` is empty, so no work is lost.
- [ ] Tag the tip first: `git tag archive/feat-agent-fleet origin/feat/agent-fleet && git push origin archive/feat-agent-fleet`.
- [ ] Wilson confirms the step 2 deploy and the retargets, then removes `feat/agent-fleet` from the `Delete Protection` ruleset (which blocks deleting it), deletes the `fleet-fast-gate` ruleset, and deletes the branch.

## Rollback

- **Before step 4:** revert the AFX-125 squash commit on `main` through a PR (restores the fleet workflows and scripts), unset `AFX_DEPLOY_SYNC_MODE`, turn Linear's "PR merged → In Staging" back on, and restore the `main-merge-commits-only` rule.
- **After step 4:** do the same, and recreate the branch from the tag: `git push origin archive/feat-agent-fleet:refs/heads/feat/agent-fleet`.

Nothing here migrates tickets or archives states automatically. Each step is Wilson's.
