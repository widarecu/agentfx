"""Delivery guidance contracts (AFX-126).

After the AFX-125 cutover there is one branch, `main`. These tests pin that
the canonical runbook (`planning/runbooks/delivery-pipeline.md`) covers the
whole flow, that every active doc, skill and template an agent reads points
at it, teaches the same commands and targets `main`, and that none of them
still teaches the retired `feat/agent-fleet` flow. Historical records
(ROADMAP, incident notes, change logs) are not checked.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL = "planning/runbooks/delivery-pipeline.md"

_ACTIVE = [
    "CLAUDE.md",
    "README.md",
    ".claude/skills/work-issue/SKILL.md",
    ".claude/skills/file-issue/SKILL.md",
    ".claude/skills/pr-preflight/SKILL.md",
    ".claude/rules/ci-and-branch-flow.md",
    ".claude/rules/migrations.md",
    ".claude/hooks/guard-bash.py",
    "config/linear/workspace.yaml",
    "planning/runbooks/branch-flow.md",
    "planning/runbooks/cloud-agent-setup.md",
    "planning/runbooks/linear-operating-model.md",
    _CANONICAL,
]

# Docs that must send a reader to the canonical runbook.
_LINKS_CANONICAL = [
    "CLAUDE.md",
    "README.md",
    ".claude/skills/work-issue/SKILL.md",
    ".claude/skills/file-issue/SKILL.md",
    ".claude/skills/pr-preflight/SKILL.md",
    ".claude/rules/ci-and-branch-flow.md",
    "planning/runbooks/branch-flow.md",
    "planning/runbooks/cloud-agent-setup.md",
    "planning/runbooks/linear-operating-model.md",
    "planning/runbooks/pe-managed-agents-setup.md",
]

# Docs an agent follows step by step to work a ticket.
_WORKFLOW_DOCS = [
    "CLAUDE.md",
    ".claude/skills/work-issue/SKILL.md",
    "planning/runbooks/linear-operating-model.md",
    _CANONICAL,
]

_RETIRED = [
    "feat/agent-fleet",
    "promote PR",
    "agent-fleet-ci",
    "agent-fleet-promote",
    "agent-fleet-state-on-promote",
    "main-to-fleet-backmerge",
    "fleet_promote_pr",
    "back-merge",
    "Wilson promotes",
]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text()


def _pe_prompt() -> str:
    text = _read("planning/runbooks/pe-managed-agents-setup.md")
    return text[text.index("## Config") : text.index("## Change log")]


@pytest.mark.parametrize("rel", _ACTIVE)
def test_active_docs_do_not_teach_the_fleet_flow(rel: str) -> None:
    text = _read(rel)
    if rel == ".claude/hooks/guard-bash.py":
        # The force-push guard may keep naming the old branch until it is deleted.
        text = "\n".join(
            line for line in text.splitlines() if "force" not in line.lower()
        )
    if rel == "planning/runbooks/branch-flow.md":
        text = text.split("\n\n", 2)[-1]  # only the pointer, not the retirement note
    found = [term for term in _RETIRED if term in text]
    assert not found, f"{rel} still teaches the retired flow: {found}"


def test_hosted_pe_prompt_copy_targets_main() -> None:
    prompt = _pe_prompt()
    assert not [term for term in _RETIRED if term in prompt]
    assert "git checkout main" in prompt
    assert "open the PR into `main`" in prompt


@pytest.mark.parametrize("rel", _LINKS_CANONICAL)
def test_docs_link_the_canonical_runbook(rel: str) -> None:
    assert "delivery-pipeline.md" in _read(rel), f"{rel} does not link {_CANONICAL}"


def test_branch_flow_is_only_a_pointer() -> None:
    text = _read("planning/runbooks/branch-flow.md")
    assert "(delivery-pipeline.md)" in text
    assert len(text.splitlines()) < 10


def test_agents_md_is_a_short_pointer_to_repo_guidance() -> None:
    text = _read("AGENTS.md")
    assert len(text.splitlines()) < 15
    assert "CLAUDE.md" in text
    assert "Read before changing" in text


@pytest.mark.parametrize("rel", _WORKFLOW_DOCS)
def test_workflow_docs_teach_the_canonical_commands(rel: str) -> None:
    text = _read(rel)
    assert "afx.py start AFX-" in text or "afx start AFX-" in text, rel
    assert "afx.py block AFX-" in text or "afx block" in text, rel
    assert "into `main`" in text, rel


@pytest.mark.parametrize("rel", [".claude/skills/work-issue/SKILL.md", _CANONICAL])
def test_branch_off_main(rel: str) -> None:
    text = _read(rel)
    assert "git fetch origin main" in text
    assert re.search(r"git checkout -b agent/AFX-<n>-<slug> origin/main", text)


def test_preflight_diffs_against_main() -> None:
    text = _read(".claude/skills/pr-preflight/SKILL.md")
    assert "git diff --stat origin/main...HEAD" in text
    assert "PR targets `main`" in text


def test_commands_the_docs_teach_exist() -> None:
    cli = _read("scripts/afx.py")
    for action in ('"start"', '"block"', '"deploy-sync"', '"recover"'):
        assert action in cli


def test_canonical_runbook_covers_the_flow() -> None:
    text = _read(_CANONICAL)
    for section in (
        "Who owns each status",
        "Review: normal and Money-path",
        "## 5. CI",
        "Retries",
        "Daily reconciliation",
        "Deploy and Done",
        "Wilson's part",
    ):
        assert section in text, section
    for phrase in (
        "Exactly one AFX ID in the title and branch",
        "Squash merge only",
        "AFX_DEPLOY_SYNC_MODE",
        "`apply`",
        "Then **stop**",
        "Never fix ticket states by hand",
    ):
        assert phrase in text, phrase


def test_canonical_runbook_names_an_owner_for_every_status() -> None:
    text = _read(_CANONICAL)
    table = text[text.index("## 2. Who owns") : text.index("## 3.")]
    rows = {
        cell.strip()
        for line in table.splitlines()
        if line.startswith("| ") and not line.startswith("| Status")
        for cell in line.split("|")[1].split(",")
    }
    workspace = yaml.safe_load(_read("config/linear/workspace.yaml"))
    states = {s["name"] for s in workspace["states"]}
    missing = {s for s in states - {"Duplicate"} if not any(s in row for row in rows)}
    assert not missing, f"no owner row for {missing}"
    assert "Done | Deploy sync only" in table


def test_retired_states_are_gone_from_the_workspace() -> None:
    workspace = yaml.safe_load(_read("config/linear/workspace.yaml"))
    names = {s["name"] for s in workspace["states"]}
    assert not names & {"In Staging", "Needs Input"}
    assert "In Staging" in _read(_CANONICAL) and "retired" in _read(_CANONICAL)


def test_canonical_runbook_explains_failed_deploy_recovery() -> None:
    text = _read(_CANONICAL)
    section = text[text.index("## 6. Deploy and Done") : text.index("## 7.")]
    assert "Blocked to Ready" in section
    assert "afx start" in section
    assert "same ticket" in section


def test_no_doc_claims_body_references_are_safe() -> None:
    """Linear acts on magic words in the PR body as well as the title."""
    for rel in _ACTIVE + ["planning/runbooks/pe-managed-agents-setup.md"]:
        text = _read(rel)
        assert 'go in the body under "Related"' not in text, rel
        assert "Mention related issues in the PR body instead" not in text, rel


@pytest.mark.parametrize("rel", _WORKFLOW_DOCS)
def test_workflow_docs_teach_one_shared_claim(rel: str) -> None:
    """AFX-142: every taker claims the same way and names itself."""
    text = _read(rel)
    assert "--taker" in text and "--link" in text, rel
    assert "needs `LINEAR_API_KEY`" not in text, rel


def test_canonical_runbook_explains_the_claim() -> None:
    text = _read(_CANONICAL)
    section = text[text.index("### How the claim works") : text.index("## 2.")]
    for phrase in (
        "exactly one starts",
        "Assignee stays Wilson and delegate is never set",
        "afx-request.yml",
        "tells the worker not to claim again",
    ):
        assert phrase in section, phrase
