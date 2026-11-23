# Delivery cutover checklist (AFX-117)

Wilson's manual Linear and GitHub settings for the move to one branch (`main`) with one owner per ticket status. **Do these on cutover day with AFX-125, not before.** Until then the current flow (`feat/agent-fleet` → promote → `main`) and its automation stay on.

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
- [ ] Run `python scripts/linear/bootstrap.py --dry-run`, then without `--dry-run`. Expected: state descriptions updated, nothing created except a missing state, nothing archived.

## Cutover day (with AFX-125)

### Linear: Settings → Integrations → GitHub

- [ ] **Keep** "PR opened → In Review".
- [ ] **Turn off** every merge-driven status change (today "PR merged → In Staging", and any "merged → Done"). Deploy sync now sets Done after a verified staging deploy.
- [ ] Screenshot the settings page after the change and attach it to AFX-125 as evidence.

### GitHub: branch protection on `main`

- [ ] Add `CI required` (from `.github/workflows/ci.yml`) as a required status check.

### Retire the transitional states

Do these after merge-driven automation is off, so nothing moves tickets back onto these states.

- [ ] Move each remaining **In Staging** ticket by hand: to Done if its change is verified running on staging, otherwise to In Review or Blocked.
- [ ] Move each remaining **Needs Input** ticket to Blocked and add `needs-wilson`.
- [ ] When both states are empty, archive them in Linear (Settings → Teams → AFX → Workflow). Then delete their entries from `config/linear/workspace.yaml` in a PR.

Nothing here migrates tickets or archives states automatically. Each step is Wilson's.
