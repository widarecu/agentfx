# Identities: who acts as whom in GitHub and Linear

**Status: design approved by Wilson on 2026-11-27 (AFX-163), not yet built. Sections 1 and 2 describe today. Sections 3 to 5 are the target and apply only as the migration steps in section 8 ship.** Nothing in this document has been created, rotated or revoked.

The rule Wilson set on 2026-11-26: every action in GitHub and Linear is recorded against the actor that really performed it. His approval is the gate on Money-path changes (order execution, sizing, kill switch, credentials, deploy gates), and that gate only means something if the platforms can tell him apart from an agent.

Terms used here:

- **Personal token**: a key created inside a person's account. Whatever uses it appears as that person.
- **GitHub App**: a machine account in GitHub with its own name (shown as `name[bot]`), its own permissions, and keys that mint tokens lasting one hour.
- **Linear app user**: a machine account in Linear, created by an "OAuth application" and used with `actor=app`. It is not a paid seat.
- **Ruleset**: GitHub's branch rules for `main` (what must be true before a merge).
- **Bypass**: permission to merge even though a ruleset rule is not met.

## 1. Inventory (read 2026-11-27)

No values are shown anywhere in this file. "Verified" means read from the platform or the repo on that date. "Not verified" means it could not be read from the laptop and needs Wilson to look (section 7).

### GitHub: credentials that can write

| # | Credential | Stored in | Used by | Appears in GitHub as | Type | Can do | Expires | Verified |
|---|---|---|---|---|---|---|---|---|
| G1 | `gh` login on the laptop | macOS keychain | Wilson by hand; Claude Code on the laptop | `widarecu` (Wilson) | Personal OAuth token | Scopes `repo`, `workflow`, `gist`, `read:org`. Because `widarecu` is the repo admin, this includes merging past the `main` ruleset | No expiry | Yes |
| G2 | `FLEET_BOT_PRIVATE_KEY` + `FLEET_BOT_CLIENT_ID` | GitHub environment `fleet-bot` (secret + variable), usable only by workflows on allowed branches | `pr-policy.yml`, to turn auto-merge on or off | `agentfx-fleet-bot[bot]` | GitHub App | Token requested with Contents write and Pull requests write only. What the App itself is allowed is not verified | Minted token: 1 hour. Private key: none | Partly |
| G3 | Built-in workflow token (`github.token`) | Created by GitHub for each workflow run | `pr-policy.yml` (commit status), `afx-request.yml` (claim tags, verdict status), `deploy.yml` (deploy tags), `delivery-reconcile.yml` | `github-actions[bot]` | Built-in, per run | Only what each job declares; at most Contents write and Statuses write | End of the run | Yes |
| G4 | Devin's GitHub integration | Devin (Cognition) | Devin sessions: branches, commits, PRs | `devin-ai-integration[bot]` | GitHub App owned by Cognition | Not verified (set in GitHub → Settings → Applications) | Managed by Devin | Identity yes, permissions no |
| G5 | `GH_TOKEN` in Devin sessions | Devin's secrets | Devin sessions, to read claim verdicts. PR #199 shows a Devin session opened a PR as `widarecu`, so some Devin session held a token of Wilson's that can write | Not verified; at least once `widarecu` | Not verified | Not verified | Not verified | No |
| G6 | `GITHUB_TOKEN` for cloud Claude Code | The cloud environment's secrets | Cloud Claude Code sessions | `widarecu` (the old setup told Wilson to create it in his account) | Fine-grained personal token | As documented: this repo only; Contents write, Pull requests write, Metadata read | Not verified | No (cannot be read from here) |
| G7 | `GITHUB_TOKEN` for the hosted PE | Railway variable `GITHUB_TOKEN` on the backend, sent to each Managed Agents session; a copy in the Managed Agents vault | The hosted Platform Engineer agent (`agentfx-fleet-pe`) | Not verified. `config.py` describes it as a fine-grained personal token for this repo, and `widarecu` is the only user on the repo, so almost certainly `widarecu` | Fine-grained personal token | Not verified | Not verified | No |
| G8 | GitHub MCP connector of the hosted PE | Anthropic Console (agent config) | Hosted PE | Not verified | Not verified | Every tool enabled | Not verified | No |
| G9 | Linear's GitHub integration | Linear | Linking PRs to tickets | `linear[bot]` (comments only) | GitHub App owned by Linear | Not verified | Managed by Linear | No |

Also checked: the repo has one collaborator (`widarecu`, admin), no deploy keys and no webhooks. The repo-level Actions secrets are `LINEAR_API_KEY`, `RAILWAY_TOKEN`, `RAILWAY_BACKEND_URL`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`.

### Linear: credentials that can write

| # | Credential | Stored in | Used by | Appears in Linear as | Type | Can do | Expires | Verified |
|---|---|---|---|---|---|---|---|---|
| L1 | `LINEAR_API_KEY` | GitHub Actions secret (set 2026-11-25) | The pipeline: claims (`afx-request.yml`), `pr-policy`, deploy sync, daily reconcile | **Wilson Reyes.** The claim comment on AFX-163, written by `afx-request.yml` on 2026-11-27, is recorded as authored by Wilson | Personal API key | Everything Wilson can do, including moving tickets into Ready | No expiry | Yes |
| L2 | Linear connector in Claude Code on the laptop | Claude's Linear connector (OAuth, signed in as Wilson) | Wilson by hand; Claude Code on the laptop | Wilson Reyes | Personal OAuth | Everything Wilson can do (admin) | Managed by Linear | Yes |
| L3 | `LINEAR_API_KEY` for cloud Claude Code | The cloud environment's secrets | Cloud Claude Code sessions | Wilson Reyes (the old setup told Wilson to create a personal key) | Personal API key | Everything Wilson can do | No expiry | No |
| L4 | `LINEAR_API_KEY` on the backend | Railway variable | In-platform fleet (QA, PE, PM, Research) filing findings | Tickets filed by the fleet show as **AgentFX Fleet**, so this is most likely that app user's token. One identity for all roles | Not verified (app token or key) | Not verified | Not verified | No |
| L5 | `LINEAR_AGENT_CLIENT_ID` + `LINEAR_AGENT_CLIENT_SECRET` | Railway variables | The PE's Linear agent (delegation webhook, session replies) | **AgentFX Platform Engineer** (inferred from the user list) | OAuth application, `actor=app` | Scopes requested in code: read, write, assignable, mentionable | Token about 30 days, renewed automatically. Client secret: none | Partly |
| L6 | `LINEAR_AGENT_WEBHOOK_SECRET` | Railway variable | Checks that webhook calls really come from Linear | Nobody (it cannot write) | Signing secret | Nothing | None | No |
| L7 | Devin's Linear integration | Devin (Cognition) | Devin sessions: tickets, comments | **Devin** (app user) | OAuth application owned by Cognition | Not verified | Managed by Devin | Identity yes, scopes no |
| L8 | Linear connector of the hosted PE | Anthropic Console (agent config) | Hosted PE | Not verified | Not verified | Every tool enabled | Not verified | No |
| L9 | `bootstrap` personal API key | Wilson's machine, if it still exists | `scripts/linear/bootstrap.py` | Wilson Reyes | Personal API key | Everything Wilson can do | No expiry | No. The operating model asked for it to be revoked |

Users in the Linear workspace: Wilson Reyes (admin), and the app users Devin, AgentFX Fleet, AgentFX Platform Engineer and Linear's own.

### Other platforms (listed for completeness, not designed here)

| Credential | Stored in | Used by |
|---|---|---|
| `RAILWAY_TOKEN` | GitHub Actions secret | Backend deploy |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | GitHub Actions secrets | Frontend deploy |
| Railway connector (read-only tools) | Anthropic Console | Hosted PE |
| `ANTHROPIC_API_KEY` | Railway; the cloud environment | Fleet agents; cloud Claude Code |
| `DEVIN_API_KEY`, `DEVIN_ORG_ID` | Railway | The backend, to start Devin sessions |
| `OANDA_API_KEY`, `OANDA_ACCOUNT_ID` (practice only) | Railway; the cloud environment. `ci.yml` reads them as Actions secrets, but no secret of that name is listed on the repo | Paper trading; integration tests |

## 2. What is wrong today

1. **Four agent paths write to GitHub as Wilson**: the laptop (G1), cloud Claude Code (G6), the hosted PE (G7, almost certainly) and at least one Devin session (G5). Of the last 30 PRs, 8 were authored by `widarecu`; the rest by Devin's App.
2. **The pipeline writes to Linear as Wilson** (L1). Every claim, every move to Done and every Blocked move is recorded as done by him. The daily check for "Backlog/Ready moves not made by Wilson" trusts exactly the identity the pipeline holds.
3. **Wilson cannot approve a PR authored as Wilson.** GitHub does not let an author approve their own PR. This blocked PR #199 and PR #207, and PRs #207 to #209 were merged by hand.
4. **Any holder of the laptop token can merge past the `main` ruleset**, because the ruleset lets the admin role bypass on a pull request and the token is Wilson's. Today that is prevented only by instruction.
5. **A code-owner approval survives later pushes.** The `main` ruleset has "dismiss stale reviews" off. `pr-policy` re-checks approval of the latest commit only for tickets labelled Money-path. So on a Normal-risk ticket that touches a code-owned file, Wilson's approval of an early commit still counts after the agent pushes more.
6. **All in-platform roles share one Linear identity** (L4); the role is only a label any actor can set.
7. **Leftover:** the `fleet-fast-gate` ruleset still exists for the deleted `feat/agent-fleet` branch.

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

1. **Ruleset on `main`: turn on "dismiss stale pull request approvals when new commits are pushed".** Closes problem 5: every approval then refers to the commit that merges.
2. **Ruleset bypass list stays "admin role, pull request only", and Wilson stays the only admin.** No App is ever added to the bypass list. This is a standing rule for the new-actor checklist.
3. **`pr-policy` approver.** Keep one named approver, but take it from the repo's code owners rather than a login written in the script, and accept a review only from a person's account with admin permission on the repo, never from an App. The login `widarecu` then lives in one place (`.github/CODEOWNERS`), which is itself code-owned.
4. **`pr-policy` when author and approver are the same person:** fail with a clear message naming the by-hand merge, and never enable auto-merge on such a PR when the ticket is Money-path or the diff is code-owned.
5. **Reconcile digest:** list every merge to `main` not made by `agentfx-fleet-bot` (that is, every by-hand merge) so each one is seen the next morning. With the pipeline on its own Linear identity (AgentFX Pipeline), "moves not made by Wilson" also becomes a true statement.
6. **Delete the `fleet-fast-gate` ruleset.**

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

## 7. What only Wilson can check

These complete the "Not verified" rows. Report the account name, type, permissions and expiry, never the value.

- GitHub → Settings → Applications → Installed GitHub Apps: the permissions of `agentfx-fleet-bot`, `devin-ai-integration` and Linear (G2, G4, G9).
- GitHub → Settings → Developer settings → Personal access tokens: every token, what it can do, when it expires, and which of G5, G6, G7 it is.
- Devin → secrets and integrations: which token is `GH_TOKEN` (G5), and whether Devin can be made to use only its App for every session.
- The cloud Claude Code environment: which `GITHUB_TOKEN` and `LINEAR_API_KEY` it holds (G6, L3).
- Railway → backend variables: which account `GITHUB_TOKEN` and `LINEAR_API_KEY` belong to (G7, L4).
- Anthropic Console → `agentfx-fleet-pe`: how its GitHub and Linear connectors sign in, and what the vault holds (G7, G8, L8).
- Linear → Settings → API: the list of personal API keys (L1, L3, L9) and of OAuth applications with their permissions (L4, L5, L7).
- A test in Linear: can an app user with only "create issues" and "create comments" set labels when it files a ticket?
- Whether the Actions secrets `OANDA_API_KEY` and `OANDA_ACCOUNT_ID` exist under another scope.

## 8. Migration plan

Each step is one PR or one settings change and leaves the pipeline working. Steps marked **W** are settings only Wilson can apply. The tickets were filed in Triage on 2026-11-27.

| # | Ticket | Step | Kind |
|---|---|---|---|
| 1 | AFX-166 | Complete the inventory: Wilson reads the items in section 7, and section 1 is updated | W + docs PR |
| 2 | AFX-167 | Ruleset on `main`: dismiss stale approvals on push; delete `fleet-fast-gate` | W |
| 3 | AFX-168 | `pr-policy`: clear result when the author is the approver; approver taken from code owners; accept only a person's review | PR (code-owned, Money-path) |
| 4 | AFX-169 | Pipeline's Linear identity: create "AgentFX Pipeline" (W), teach the workflows to use its app token, then delete the personal key behind `LINEAR_API_KEY` (W) | W + PR + W |
| 5 | AFX-170 | Reconcile digest: use real actors, and list by-hand merges to `main` | PR |
| 6 | AFX-171 | Devin: remove any token of Wilson's from Devin's secrets; if a token is still needed to read claim verdicts, make it read-only | W |
| 7 | AFX-172 | Hosted PE on GitHub: create `agentfx-pe` (W), have the backend mint an App token per session in place of the personal token, then delete the personal token (W) | W + PR + W |
| 8 | AFX-173 | Cloud Claude Code: create `agentfx-claude` and "AgentFX Claude" (W), update the cloud setup, then delete the personal token and key (W) | W + docs PR + W |
| 9 | AFX-174 | In-platform roles in Linear: create the per-role app users (W), have the fleet file as its role, retire "AgentFX Fleet" (W) | W + PR + W |
| 10 | AFX-175 | Narrow the Linear permissions of filing-only agents to read, create issues, create comments, if the test in section 7 passes | W |
| 11 | AFX-176 | Revoke the `bootstrap` Linear key if it still exists; mark this document as current state | W + docs PR |
