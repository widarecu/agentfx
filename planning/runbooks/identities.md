# Identities: who acts as whom in GitHub and Linear

**Status: design approved by Wilson on 2026-11-27 (AFX-163), not yet built. Sections 1 and 2 describe today. Sections 3 to 5 are the target and apply only as the migration steps in section 8 ship.** Nothing in this document has been created, rotated or revoked.

The rule Wilson set on 2026-11-26: every action in GitHub and Linear is recorded against the actor that really performed it. His approval is the gate on Money-path changes (order execution, sizing, kill switch, credentials, deploy gates), and that gate only means something if the platforms can tell him apart from an agent.

**Read only the part you need:** which identity an actor uses → section 3. How a Money-path PR gets approved → section 4. Adding an actor, rotating or revoking → section 5. Which credential is stored where → section 1. Sections 2, 6, 7 and 8 are background for the migration.

Terms used here:

- **Personal token**: a key created inside a person's account. Whatever uses it appears as that person.
- **GitHub App**: a machine account in GitHub with its own name (shown as `name[bot]`), its own permissions, and keys that mint tokens lasting one hour.
- **Linear app user**: a machine account in Linear, created by an "OAuth application" and used with `actor=app`. It is not a paid seat.
- **Ruleset**: GitHub's branch rules for `main` (what must be true before a merge).
- **Bypass**: permission to merge even though a ruleset rule is not met.

## 1. Inventory (read 2026-11-27)

No values are shown anywhere in this file. "Verified" means read from the platform or the repo on that date. "Not verified" means nobody has been able to read it yet (section 7).

### GitHub: credentials that can write

| # | Credential | Stored in | Used by | Appears in GitHub as | Type | Can do | Expires | Verified |
|---|---|---|---|---|---|---|---|---|
| G1 | `gh` login on the laptop | macOS keychain | Wilson by hand; Claude Code on the laptop | `widarecu` (Wilson) | Personal OAuth token | Scopes `repo`, `workflow`, `gist`, `read:org`. Because `widarecu` is the repo admin, this includes merging past the `main` ruleset | No expiry | Yes |
| G2 | `FLEET_BOT_PRIVATE_KEY` + `FLEET_BOT_CLIENT_ID` | GitHub environment `fleet-bot` (secret + variable), usable only by workflows on `main` | `pr-policy.yml`, to turn auto-merge on or off | `agentfx-fleet-bot[bot]` | GitHub App owned by `widarecu` | Installed on `widarecu/agentfx` only. Metadata read; code and pull requests read and write | Minted token: 1 hour. Private key: none | Yes |
| G3 | Built-in workflow token (`github.token`) | Created by GitHub for each workflow run | `pr-policy.yml` (commit status), `afx-request.yml` (claim tags, verdict status), `deploy.yml` (deploy tags), `delivery-reconcile.yml` | `github-actions[bot]` | Built-in, per run | Only what each job declares; at most Contents write and Statuses write | End of the run | Yes |
| G4 | Devin's GitHub integration ("Devin.ai Integration") | Devin (Cognition) | Devin sessions: branches, commits, PRs | `devin-ai-integration[bot]` | GitHub App owned by Cognition (`usacognition`) | Installed on **all repositories**. Read and write on code, pull requests, workflows, actions, checks, commit statuses, issues, discussions, projects and repository creation; read on security alerts and more. The permission list is Cognition's; only the repository selection is Wilson's to narrow | Managed by Devin | Yes |
| G5 | Devin's App acting on Wilson's behalf | No stored token: "Devin.ai Integration" is listed under Wilson's Authorized GitHub Apps, which lets it act as him | Devin sessions a person starts, when Devin's "Open PRs as" setting is User | `widarecu`. PRs #192, #195 and #199 (2026-11-25) were created this way; the session's command-line sign-in is the bot's | User authorization of a GitHub App | Whatever the App can do, recorded as Wilson | Until Wilson revokes the authorization | Yes. Devin's "Open PRs as" is Devin as of 2026-11-27; not yet observed on a new PR (AFX-171) |
| G6 | Cloud Claude Code | None | Not in use: Wilson uses Claude Code only on the laptop (2026-11-27) | Nothing | None exists (one personal token on the account, and it is G7) | Nothing | n/a | Yes |
| G7 | `GITHUB_TOKEN` for the hosted PE (personal token `agentfx-fleet-pe`) | Railway variable `GITHUB_TOKEN` on the backend, sent to each Managed Agents session; a copy in the vault `agentfx-fleet-pe-vault` in the Claude Console | The hosted Platform Engineer agent (`agentfx-fleet-pe`) | `widarecu` | Fine-grained personal token, the only personal token on the account (no classic tokens) | `widarecu/agentfx` only. Metadata read; code, pull requests and **workflows** read and write | **2027-01-28** (created 2026-10-30) | Yes |
| G8 | GitHub connector of the hosted PE (`api.githubcopilot.com/mcp`) | Vault `agentfx-fleet-pe-vault` in the Claude Console | Hosted PE | `widarecu` (an OAuth sign-in Wilson made) | OAuth sign-in | Every connector tool is enabled in the agent's configuration | Managed by the connector (credential updated 2026-11-19) | Yes |
| G9 | Linear's GitHub integration ("Linear Code") | Linear | Linking PRs to tickets | Linear's App; it is also under Wilson's Authorized GitHub Apps, so it can act as `widarecu` | GitHub App owned by Linear | Installed on **all repositories**. Read and write on code, pull requests, workflows, actions, checks, commit statuses and issues | Managed by Linear | Yes |

Also checked: the repo has one collaborator (`widarecu`, admin), no deploy keys and no webhooks. The repo-level Actions secrets are `LINEAR_API_KEY`, `LINEAR_PIPELINE_CLIENT_ID`, `LINEAR_PIPELINE_CLIENT_SECRET`, `RAILWAY_TOKEN`, `RAILWAY_BACKEND_URL`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`; any workflow on any pushed branch can read them (AFX-179). Wilson's Authorized OAuth Apps: GitHub CLI (the laptop, G1) and Visual Studio Code are in use; Cursor, Devin Desktop, GitHub iOS and Resend have never been used. Copilot SWE Agent is authorized but Wilson does not use it.

### Linear: credentials that can write

| # | Credential | Stored in | Used by | Appears in Linear as | Type | Can do | Expires | Verified |
|---|---|---|---|---|---|---|---|---|
| L1 | `LINEAR_PIPELINE_CLIENT_ID` / `LINEAR_PIPELINE_CLIENT_SECRET` (the old personal `LINEAR_API_KEY` secret stays only until Wilson deletes it after live verification) | GitHub Actions secrets | The pipeline: claims (`afx-request.yml`), `pr-policy`, deploy sync, daily reconcile | **AgentFX Pipeline** (app user). Before AFX-169 it was Wilson Reyes: the claim comment on AFX-163 of 2026-11-27 is recorded as authored by Wilson | "AgentFX Pipeline" OAuth application, client-credentials grant | Read and write | Token about 30 days, minted per run. Client secret: none | Yes. Live since 2026-11-27: the Done comment on AFX-169 and the claim on AFX-166 are recorded as AgentFX Pipeline |
| L2 | Linear connector in Claude Code on the laptop | Claude's Linear connector (OAuth, signed in as Wilson) | Wilson by hand; Claude Code on the laptop | Wilson Reyes | Personal OAuth | Everything Wilson can do (admin) | Managed by Linear | Yes |
| L3 | Cloud Claude Code | None | Not in use (see G6) | Nothing | None exists (the two personal keys on the workspace are both pipeline keys) | Nothing | n/a | Yes |
| L4 | `LINEAR_API_KEY` on the backend | Railway variable | In-platform fleet (QA, PE, PM, Research) filing findings and reading the Ready queue | **AgentFX Fleet** (app user). One identity for all roles | A token of the "AgentFX Fleet" application, pasted in by hand. Confirmed not a personal key; the token's kind and lifetime are not confirmed | Not verified | Rejected by Linear (401) since 2026-11-27 00:10 UTC, most likely expired (AFX-177) | Partly |
| L5 | `LINEAR_AGENT_CLIENT_ID` + `LINEAR_AGENT_CLIENT_SECRET` | Railway variables | The PE's Linear agent (delegation webhook, session replies) | **AgentFX Platform Engineer** (app user, installed 2026-11-05) | OAuth application, `actor=app` | Scopes requested in code: read, write, assignable, mentionable | Token about 30 days, renewed automatically. Client secret: none | Yes (presence and application; scopes from the code) |
| L6 | `LINEAR_AGENT_WEBHOOK_SECRET` | Railway variable | Checks that webhook calls really come from Linear | Nobody (it cannot write) | Signing secret | Nothing | None | Yes (present) |
| L7 | Devin's Linear integration | Devin (Cognition) | Devin sessions: tickets, comments | **Devin** (app user) | OAuth application owned by Cognition | Not verified | Managed by Devin | Identity yes, scopes no |
| L8 | Linear connector of the hosted PE (`mcp.linear.app/mcp`) | Vault `agentfx-fleet-pe-vault` in the Claude Console | Hosted PE | **Wilson Reyes** (an OAuth sign-in Wilson made; "MCP" on Linear's Applications page) | OAuth sign-in | Every connector tool is enabled; everything Wilson's sign-in allows | Managed by the connector (credential updated 2026-11-19) | Yes |
| L9 | Old personal API keys | Linear → Settings → API | Nothing now | Wilson Reyes | Personal API keys | The `bootstrap` key no longer exists. "agentfx-github-actions" (2 permissions, one team, created 2026-11-11) was the previous pipeline key, last used 2026-11-18. "GitHuib Actions Key" (4 permissions, one team, created 2026-11-25) is the `LINEAR_API_KEY` Actions secret that L1 replaces | No expiry | Yes. Both are for Wilson to revoke (AFX-169 step 3, AFX-176) |

Users in the Linear workspace: Wilson Reyes (admin), and the app users Devin, AgentFX Fleet, AgentFX Platform Engineer, AgentFX Pipeline and Linear's own. OAuth applications owned by the workspace: AgentFX Fleet, AgentFX Platform Engineer, AgentFX Pipeline.

### Other platforms (listed for completeness, not designed here)

| Credential | Stored in | Used by |
|---|---|---|
| `RAILWAY_TOKEN` | GitHub Actions secret | Backend deploy |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | GitHub Actions secrets | Frontend deploy |
| Railway connector (an OAuth sign-in Wilson made; nine read-only tools enabled) | Vault `agentfx-fleet-pe-vault` in the Claude Console | Hosted PE |
| `ANTHROPIC_API_KEY` | Railway | Fleet agents |
| `DEVIN_API_KEY`, `DEVIN_ORG_ID` | Railway | The backend, to start Devin sessions |
| `OANDA_API_KEY`, `OANDA_ACCOUNT_ID` (practice only) | Railway. `ci.yml` reads them as Actions secrets, but no secret of that name is listed on the repo | Paper trading; integration tests |

## 2. What is wrong today

1. **Three agent paths write to GitHub as Wilson**: the laptop (G1), the hosted PE (its token G7 and its connector G8) and Devin sessions a person starts (G5). Of the 30 PRs up to #210, 8 were authored by `widarecu`: 5 from the laptop and 3 from Devin acting on his behalf. The hosted PE also writes to Linear as Wilson through its connector (L8).
2. **Fixed 2026-11-27 (AFX-169):** the pipeline used to write to Linear as Wilson (L1), so every claim, Done move and Blocked move was recorded as done by him. It now authenticates as the "AgentFX Pipeline" app user; the personal key is removed once that is proven live.
3. **Wilson cannot approve a PR authored as Wilson.** GitHub does not let an author approve their own PR. This blocked PR #199 and PR #207, and PRs #207 to #209 were merged by hand.
4. **Any holder of the laptop token can merge past the `main` ruleset**, because the ruleset lets the admin role bypass on a pull request and the token is Wilson's. Today that is prevented only by instruction.
5. **Fixed 2026-11-27 (AFX-167):** a code-owner approval used to survive later pushes, so on a Normal-risk ticket touching a code-owned file Wilson's approval of an early commit still counted after the agent pushed more. The `main` ruleset now dismisses approvals on every new push.
6. **All in-platform roles share one Linear identity** (L4); the role is only a label any actor can set.
7. **Fixed 2026-11-27 (AFX-167):** the leftover `fleet-fast-gate` ruleset for the deleted `feat/agent-fleet` branch is deleted.

## 3. Target model

One row per actor. Each has exactly one GitHub identity and one Linear identity. Rows marked "new" do not exist yet. Section 6 records the three choices Wilson made.

| Actor | GitHub identity | GitHub permissions | Linear identity | Linear permissions |
|---|---|---|---|---|
| Wilson, by hand | `widarecu` | Admin. The only identity that can approve, and the only one on the ruleset's bypass list | Wilson Reyes | Admin. The only identity that moves tickets out of Triage, into Ready, or sets priority |
| Wilson through Claude Code on the laptop | `widarecu` (decision 1) | Same account. Normal-risk PRs auto-merge as now | Wilson Reyes | Same account |
| Devin | `devin-ai-integration[bot]` (exists) | Contents and Pull requests write on this repo. No personal token of Wilson's in any session | Devin (exists) | Read, create issues, create comments |
| Cloud Claude Code sessions | `agentfx-claude[bot]` (new GitHub App) | Contents write, Pull requests write, Metadata read, this repo only | AgentFX Claude (new app user) | Read, create issues, create comments |
| Hosted PE (Managed Agents) and the in-platform PE role | `agentfx-pe[bot]` (new GitHub App) | Contents write, Pull requests write, Metadata read, this repo only | AgentFX Platform Engineer (exists) | Read, write (it replies in agent sessions), assignable, mentionable |
| In-platform QA agent | None. It does not write to GitHub | None | AgentFX QA (new app user) | Read, create issues, create comments |
| In-platform PM agent | None | None | AgentFX PM (new app user) | Read, create issues, create comments |
| In-platform Research agent | None | None | AgentFX Research (new app user), or retire the role if unused | Read, create issues, create comments |
| Pipeline automation (merge, claim, deploy sync, reconcile) | `agentfx-fleet-bot[bot]` (exists) for merges. Commit statuses and tags keep using the built-in workflow token, which shows as `github-actions[bot]`, lasts one run and cannot be held by anyone else | Contents write, Pull requests write | AgentFX Pipeline (new app user) | Read, write. The only machine identity that changes ticket status |

"AgentFX Fleet" is retired once QA, PM and Research have their own app users.

### How the practices are met

- **Humans use personal accounts; machines use app identities.** After migration no personal token or personal API key of Wilson's is stored anywhere except his own laptop keychain and his own browser.
- **Least privilege.** No agent identity is a repo collaborator, so none can approve with effect, bypass the ruleset or change settings. In Linear, agents that only file and comment get the narrow "create issues" and "create comments" permissions instead of general write, so they cannot change a ticket's status at all: "agents cannot move tickets into Ready" becomes something Linear enforces. They claim work through `afx start`, which already has a keyless path that asks the pipeline to do it. Whether Linear's narrow permissions cover adding labels at creation needs a test before relying on this (section 7).
- **No shared credentials.** One credential per actor, stored in one place. The hosted PE and cloud Claude Code stop sharing an account with each other and with Wilson.
- **Attribution from the platform.** The Source label and signed comments stay as a convenience for reading, but checks (the reconcile digest, `pr-policy`) read the recorded author or actor.
- **Rotation and revocation:** section 5.

## 4. The approval gate with honest identities

What must be true for a Money-path PR, or any PR touching a path in `.github/CODEOWNERS`, to merge:

| PR author | Who approves | How GitHub enforces it |
|---|---|---|
| `devin-ai-integration[bot]`, `agentfx-claude[bot]`, `agentfx-pe[bot]` | Wilson approves the latest commit in GitHub | `pr-policy` (required check) passes a Money-path ticket only with the approver's review of the latest commit. The ruleset requires a code-owner review for code-owned paths. Auto-merge then runs as `agentfx-fleet-bot` |
| `widarecu` (Wilson by hand or through the laptop) | Nobody can: GitHub refuses an author's own approval | Wilson merges it himself (decision 1). `pr-policy` should say so plainly ("authored by the approver: merge by hand") instead of waiting for an approval that cannot arrive |
| Anyone else | Not accepted | No other identity has write access to the repo |

Changes proposed to make this hold without relying on convention:

1. **Ruleset on `main`: "dismiss stale pull request approvals when new commits are pushed" is on** (applied 2026-11-27). Every approval refers to the commit that merges.
2. **Ruleset bypass list stays "admin role, pull request only", and Wilson stays the only admin.** No App is ever added to the bypass list. This is a standing rule for the new-actor checklist.
3. **`pr-policy` approver.** Keep one named approver, but take it from the repo's code owners rather than a login written in the script, and accept a review only from a person's account with admin permission on the repo, never from an App. The login `widarecu` then lives in one place (`.github/CODEOWNERS`), which is itself code-owned.
4. **`pr-policy` when author and approver are the same person:** fail with a clear message naming the by-hand merge, and never enable auto-merge on such a PR when the ticket is Money-path or the diff is code-owned.
5. **Reconcile digest:** list every merge to `main` not made by `agentfx-fleet-bot` (that is, every by-hand merge) so each one is seen the next morning. With the pipeline on its own Linear identity (AgentFX Pipeline), "moves not made by Wilson" also becomes a true statement.
6. **The `fleet-fast-gate` ruleset is deleted** (2026-11-27).

The limit that remains: while Claude Code on the laptop uses Wilson's own `gh` login, it holds a token that could merge by hand. Wilson accepted this on 2026-11-27 (decision 1). Giving laptop sessions their own App would remove it.

## 5. Procedures

### Adding a new actor

1. Give it a name and add a row to the table in section 3 through a PR.
2. GitHub, only if it writes code: Wilson creates a GitHub App named `agentfx-<actor>`, installed on this repo only, with Contents write, Pull requests write and Metadata read. Never add it as a collaborator or to a ruleset bypass list.
3. Linear: Wilson creates an OAuth application named "AgentFX <Actor>" and installs it with `actor=app`, with read, create issues and create comments. Add general write only if the actor must change ticket status, and say why in the PR.
4. Store each credential in exactly one place, the one the actor runs in. Add it to the inventory in section 1 with its expiry.
5. Check: the actor opens a test PR and files a test Triage issue; GitHub and Linear both show the new name, and an approval attempt by the actor has no effect on `pr-policy`.

### Rotating or revoking a credential

| Credential type | Rotate | Revoke at once |
|---|---|---|
| GitHub App private key | App settings → generate a new private key, store it, delete the old key. Tokens already minted die within the hour | Delete the key, or suspend the App's installation on the repo |
| Linear OAuth application | Application settings → rotate the client secret, store it | Revoke the application's access in Linear → Settings → Applications |
| Personal token or key (Wilson's own use only) | Create a new one, delete the old | Delete it |
| Third-party integration (Devin, Linear's GitHub App) | In that product's settings | Uninstall the integration |

After any rotation, update the "Expires" and "Verified" columns in section 1. Rotate at once if a value was ever printed in a log, a ticket or a chat.

## 6. Decisions (Wilson, 2026-11-27)

1. **Money-path and code-owned PRs from the laptop: Wilson merges them by hand.** Work through Claude Code on the laptop stays recorded as Wilson. `pr-policy` says plainly that such a PR needs a by-hand merge, and the reconcile digest lists every by-hand merge. Accepted weakness: the laptop agent holds Wilson's login, so only instruction stops it from merging; it must never merge unless Wilson says so for that PR. Rejected: a special approval that `pr-policy` accepts from the author (anything the laptop agent can reach, it could also record). Not chosen for now: a separate App for laptop sessions.
2. **In-platform agents in Linear: one app user per role** (QA, PM, Research; PE already has its own). App users are free, and each role can be revoked alone.
3. **Cloud Claude Code and the hosted PE: separate identities** (`agentfx-claude` and `agentfx-pe`). Their credentials live in different places and they are started by different things.

## 7. What is still not verified

Wilson read the settings pages on 2026-11-27 (AFX-166) and section 1 carries the results. What remains:

- **L4:** what kind of token the backend's `LINEAR_API_KEY` is and how long it was meant to last. It stops mattering once each role fetches its own token (AFX-174).
- **L7:** the permissions of Devin's Linear integration. Cognition sets them.
- **G5:** a PR from a Devin session Wilson starts himself, under today's "Open PRs as: Devin" setting (AFX-171).
- A test in Linear: can an app user with only "create issues" and "create comments" set labels when it files a ticket (AFX-175)?
- Whether the Actions secrets `OANDA_API_KEY` and `OANDA_ACCOUNT_ID` exist under another scope. `ci.yml` reads them, and no secret of that name is listed on the repo.

Cloud Claude Code is not in use (G6, L3), so its row in section 3 and step 8 of the migration describe an actor that does not exist today.

## 8. Migration plan

Eleven steps, in this order, each one PR or one settings change. Linear holds the detail of each; the table is only the order. **W** marks a step that starts with a setting only Wilson can apply.

| # | Ticket | Step |
|---|---|---|
| 1 | AFX-166 | W: complete the inventory (section 7) |
| 2 | AFX-167 | W: `main` ruleset drops approvals on new pushes; delete `fleet-fast-gate` |
| 3 | AFX-168 | `pr-policy` handles author-is-approver; approver from code owners |
| 4 | AFX-169 | W: the pipeline's own Linear identity |
| 5 | AFX-170 | Reconcile digest: real actors, by-hand merges |
| 6 | AFX-171 | W: no token of Wilson's in Devin sessions |
| 7 | AFX-172 | W: hosted PE on its own GitHub App |
| 8 | AFX-173 | W: cloud Claude Code on its own GitHub App and Linear app user |
| 9 | AFX-174 | W: one Linear app user per in-platform role |
| 10 | AFX-175 | W: narrow Linear permissions for filing-only agents |
| 11 | AFX-176 | W: revoke leftovers; mark this document as current state |
