#!/usr/bin/env python3
"""Comment on each Linear issue a PR merged into `main` carries (AFX-68).

Run by `.github/workflows/agent-fleet-promoted-to-main.yml` for every PR
merged into `main`: promotes from `feat/agent-fleet` and direct hotfixes.
It is the second comment after `fleet_promote_pr.py`'s "merged to
`feat/agent-fleet`, not on `main` yet" (AFX-69): the code is now on `main`
and deploying to staging.

1. Commits that just landed on `main`: the merge commit, plus, for a merge
   commit, the first-parent chain of its second parent that `main` didn't
   already have (`git log --first-parent <main-before>..<merge>^2`). For a
   promote that is one commit per agent PR and back-merge, the same
   granularity `fleet_promote_pr.py` lists.
2. `AFX-<n>` ids from the PR title, those commit subjects and the titles of
   the PRs that landed them (`commits/{sha}/pulls`). Back-merge PRs are
   skipped.
3. One comment per issue, skipped when the issue already has this PR's
   marker (all comment pages are read).

Nothing here may fail the workflow: no ids, no `LINEAR_API_KEY` or a
Linear error is logged and the script exits 0.

Stdlib only, plus the `gh` CLI (authenticated through `GH_TOKEN`):

    python3 scripts/fleet_promoted_to_main.py --pr 116 --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fleet_promote_pr import (  # noqa: E402
    BASE,
    Linear,
    _gh_json,
    _run,
    afx_ids,
    warn,
)

# Deployments list of the `agentfx` staging service (the one deploy.yml's
# `railway up --service agentfx` targets).
RAILWAY_DEPLOYS_URL = (
    "https://railway.com/project/1e8f258e-10e8-46d2-83b6-625906f688ec"
    "/service/c3e4ce31-ecc5-4ddc-8e53-9faf0cce5c1e"
)


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (unit-tested)
# ─────────────────────────────────────────────────────────────────────


def promoted_marker(pr_number: int) -> str:
    return f"<!-- afx-68:promoted-to-main:{pr_number} -->"


def promoted_comment(pr: dict, *, deploys_url: str = RAILWAY_DEPLOYS_URL) -> str:
    return (
        f"Promoted to `{BASE}` in [PR #{pr['number']}]({pr['html_url']}) "
        f"at {pr['merged_at']}.\n"
        f"Staging deploy: {deploys_url}.\n\n"
        f"{promoted_marker(pr['number'])}\n"
        "_Posted by agent-fleet-promoted-to-main.yml (AFX-68)._"
    )


def is_merged_into_main(pr: dict) -> bool:
    return (
        bool(pr.get("merged_at"))
        and (pr.get("base") or {}).get("ref") == BASE
        and (pr.get("head") or {}).get("ref") != BASE
    )


def landing_prs(candidates: list[dict], sha: str) -> list[dict]:
    """PRs merged as `sha`. Back-merge PRs (head `main`) are skipped."""
    return [
        pr
        for pr in candidates
        if pr.get("merged_at")
        and pr.get("merge_commit_sha") == sha
        and (pr.get("head") or {}).get("ref") != BASE
    ]


def collect_ids(
    pr: dict,
    commits: list[tuple[str, str]],
    pulls_for: Callable[[str], list[dict]],
) -> list[str]:
    """`AFX-<n>` ids the merge carries, first-seen order, deduplicated."""
    texts = [pr.get("title") or ""]
    for sha, subject in commits:
        texts.append(subject)
        texts += [p.get("title") or "" for p in landing_prs(pulls_for(sha), sha)]
    ids: list[str] = []
    for text in texts:
        ids += [i for i in afx_ids(text) if i not in ids]
    return ids


def post_comments(
    pr: dict,
    ids: list[str],
    linear: Linear | None,
    *,
    deploys_url: str = RAILWAY_DEPLOYS_URL,
    dry_run: bool = False,
) -> list[str]:
    """Post the comment on each issue lacking this PR's marker; ids posted."""
    body = promoted_comment(pr, deploys_url=deploys_url)
    marker = promoted_marker(pr["number"])
    if dry_run:
        for ident in ids:
            print(f"--- {ident} (dry run) ---\n{body}\n")
        return []
    if linear is None:
        return []
    posted = []
    for ident in ids:
        if linear.comment_once(ident, body, marker):
            print(f"Commented on {ident}")
            posted.append(ident)
        else:
            print(f"{ident}: already commented, unknown or Linear error; skipped")
    return posted


# ─────────────────────────────────────────────────────────────────────
# I/O
# ─────────────────────────────────────────────────────────────────────


def fetch_pr(repo: str, number: int) -> dict:
    return _gh_json("api", f"repos/{repo}/pulls/{number}")


def landed_commits(merge_sha: str) -> list[tuple[str, str]]:
    """`(sha, subject)` of the commits `merge_sha` brought onto `main`."""
    if _run("git", "cat-file", "-e", f"{merge_sha}^{{commit}}", check=False).returncode:
        _run("git", "fetch", "--quiet", "origin", merge_sha)
    shas = _run("git", "rev-list", "--parents", "-n", "1", merge_sha).stdout.split()
    parents = shas[1:]
    ranges = [["-1", merge_sha]] + [[f"{parents[0]}..{p}"] for p in parents[1:]]
    out: list[tuple[str, str]] = []
    for rng in ranges:
        log = _run("git", "log", "--first-parent", "--format=%H%x09%s", *rng).stdout
        out += [tuple(ln.split("\t", 1)) for ln in log.splitlines() if "\t" in ln]
    return out


def pulls_for_commit(repo: str) -> Callable[[str], list[dict]]:
    def pulls(sha: str) -> list[dict]:
        try:
            return _gh_json("api", f"repos/{repo}/commits/{sha}/pulls") or []
        except Exception as exc:  # noqa: BLE001 - fall back to the subject alone
            warn(f"Could not list PRs for {sha[:7]}: {exc}")
            return []

    return pulls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY", "widarecu/agentfx")
    )
    parser.add_argument("--pr", type=int, required=True, help="PR merged into main")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the comments, post nothing"
    )
    args = parser.parse_args(argv)

    pr = fetch_pr(args.repo, args.pr)
    if not is_merged_into_main(pr):
        print(f"PR #{args.pr} is not a merged PR into `{BASE}`; nothing to do.")
        return 0

    commits = landed_commits(pr["merge_commit_sha"])
    ids = collect_ids(pr, commits, pulls_for_commit(args.repo))
    print(
        f"PR #{pr['number']} landed {len(commits)} first-parent commit(s); "
        f"ids: {', '.join(ids) or '(none)'}"
    )
    if not ids:
        print(f"PR #{pr['number']} carries no AFX ids; nothing to post.")
        return 0

    linear = None
    if not args.dry_run:
        if os.environ.get("LINEAR_API_KEY"):
            linear = Linear(os.environ["LINEAR_API_KEY"])
        else:
            warn("LINEAR_API_KEY not set: no Linear comments posted.")
    post_comments(pr, ids, linear, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
