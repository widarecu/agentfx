"""Platform Engineer Agent — v0.8.1 Phase C.2 Phase 1.

Reads QA work orders, uses Opus 5 to produce a concrete fix
PROPOSAL (investigation + diff + tests + risk + expected impact),
and emails it to Wilson for manual application. This is PE as an
analyst, not yet as an autonomous committer — that's C.2 Phase 2
once we trust the judgment quality.

Why staged this way:
- The three QA work orders from 2026-10-11 (order-dup idempotency
  guard, cost-model recal, promotion-gate correlation check) are
  sitting unaddressed. Even a proposal-only PE gets Wilson a
  concrete starting point instead of just a work order title.
- Managed Agents workspace setup is non-trivial (vault creds for
  GitHub, session container lifecycle, PR workflow). Phase 1
  proves out "does PE's judgment hold up?" first.
- Reflexion-ready: each proposal is logged. When Wilson later
  flags "applied / rejected / modified," the dataset grades PE's
  quality — needed before granting autonomous PR authority.

Trigger model:
- Scan daily, event-triggered on new QA work orders.
- For each unprocessed work order: read the target file(s),
  build an Opus 5 prompt, propose a fix, write to agent_memory +
  decision log + email Wilson.
- Per-work-order idempotency via `decision_key = "pe_proposal_" +
  qa_decision_id + "_" + work_order_hash`.

Why Opus 5?
Code analysis quality matters here more than token cost. ~$0.25
per proposal × 5 proposals/month = ~$1.25/mo — well under the
fleet budget. Opus 5 is more likely to catch subtle issues
(the duplicated order bug is a good example of a race condition
only strong code reasoning catches).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agentfx.agents.fleet.llm import LLMCallResult, structured_call
from agentfx.agents.fleet.memory import (
    DecisionRecord,
    record_decision,
    write_memory_file,
)
from agentfx.api.core.config import settings
from agentfx.api.models.agent_decision import AgentDecision, AgentRole

logger = logging.getLogger(__name__)


# Hard cap: how many PE proposals we're willing to generate per
# calendar day. Opus 5 at $5/MTok input is the pricey one; keeping
# this at 3/day leaves the fleet daily spend well under $2.
_MAX_PE_PROPOSALS_PER_DAY = 3

# How many lines of a target file to feed into the prompt. Codebase
# files can run to several hundred lines; we pass the whole file but
# cap at 2000 lines to keep input tokens bounded.
_FILE_READ_MAX_LINES = 2000


def _work_order_hash(wo: dict[str, Any]) -> str:
    """Stable short hash of a work order for the decision_key.

    Uses title + target_file so the same QA report regenerating the
    same work order on the next scan dedups correctly even if the
    full spec text drifts.
    """
    key = f"{wo.get('title', '')}|{wo.get('target_file', '')}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _proposal_key_for(qa_decision_id: str, wo: dict[str, Any]) -> str:
    return f"pe_proposal_{qa_decision_id}_{_work_order_hash(wo)}"


# ─────────────────────────────────────────────────────────────────────
# Structured output schema
# ─────────────────────────────────────────────────────────────────────


class PEProposedChange(BaseModel):
    """One file-level change in the proposal."""

    file_path: str = Field(
        default="",
        description=(
            "Repo-relative path, e.g. `src/agentfx/services/paper_runner.py`."
        ),
    )
    change_type: str = Field(
        default="modify",
        description="'add' / 'modify' / 'delete'.",
    )
    rationale: str = Field(
        default="",
        description="What this change does and why.",
    )
    diff: str = Field(
        default="",
        description=(
            "Concrete code change, either as a unified diff or as "
            "'before:' / 'after:' blocks. Must be mechanically "
            "applicable by a human."
        ),
    )


class PENewTest(BaseModel):
    """A test the agent proposes to cover the fix."""

    file_path: str = Field(
        default="",
        description="Repo-relative test file path.",
    )
    name: str = Field(
        default="",
        description="Test function name, e.g. `test_dedup_blocks_duplicate_signals`.",
    )
    purpose: str = Field(
        default="",
        description="What this test proves + why it would have caught the bug.",
    )


class PEProposal(BaseModel):
    """The structured fix proposal PE produces for one QA work order."""

    investigation_summary: str = Field(
        default="",
        description=(
            "2-5 sentence narrative: what PE found when reading the "
            "code, what the actual bug / design gap is, how confident "
            "PE is in the diagnosis."
        ),
    )
    proposed_changes: list[PEProposedChange] = Field(
        default_factory=list,
        description=(
            "File-level changes. Minimum viable set — a bug fix "
            "doesn't need surrounding cleanup. Zero changes is a "
            "valid output when PE decides the work order shouldn't "
            "be actioned (explain in investigation_summary)."
        ),
    )
    new_tests: list[PENewTest] = Field(
        default_factory=list,
        description=(
            "Tests PE wants added alongside the fix. Must cover the "
            "exact failure mode QA reported, so a regression is "
            "caught deterministically."
        ),
    )
    risk_assessment: str = Field(
        default="",
        description=(
            "What could go wrong with this change: adjacent code "
            "paths, performance, migration, backwards compatibility."
        ),
    )
    estimated_profit_impact: str = Field(
        default="",
        description=(
            "Concrete profit-chain effect expected if this ships. "
            "Trace back to QA's expected_profit_impact; add any "
            "refinement based on what PE learned from the code."
        ),
    )
    confidence: str = Field(
        default="medium",
        description=(
            "'high' (ready for Wilson to apply as-is), 'medium' "
            "(wants a sanity check), 'low' (needs more investigation "
            "before acting on). Low confidence proposals should "
            "explain what's missing in the investigation summary."
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# Scan + dispatch
# ─────────────────────────────────────────────────────────────────────


def scan_and_propose(*, db: Session, now: datetime | None = None) -> int:
    """Find unprocessed QA work orders, generate PE proposals for
    them. Returns the number of proposals produced.

    Never raises. Called from the orchestrator's PE scan loop.
    """
    now = now or datetime.now(UTC)
    try:
        candidates = _collect_candidate_work_orders(db)
    except Exception:
        logger.exception("pe_agent: candidate scan failed")
        return 0

    # Rate limit — bail out early if we're at today's budget cap.
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    already_today = (
        db.query(AgentDecision)
        .filter(AgentDecision.agent_role == AgentRole.PE.value)
        .filter(AgentDecision.action == "proposed_fix")
        .filter(AgentDecision.created_at >= today_start)
        .count()
    )
    if already_today >= _MAX_PE_PROPOSALS_PER_DAY:
        logger.info(
            "pe_agent: skipping scan — daily cap already hit (%d/%d)",
            already_today,
            _MAX_PE_PROPOSALS_PER_DAY,
        )
        return 0

    budget_left = _MAX_PE_PROPOSALS_PER_DAY - already_today
    produced = 0
    for qa_decision_id, work_order in candidates[:budget_left]:
        try:
            _generate_proposal(
                db=db, qa_decision_id=qa_decision_id, work_order=work_order
            )
            produced += 1
        except Exception:
            logger.exception(
                "pe_agent: proposal generation failed for QA decision=%s "
                "work_order_title=%r",
                qa_decision_id,
                work_order.get("title"),
            )
    return produced


def _collect_candidate_work_orders(
    db: Session,
) -> list[tuple[str, dict[str, Any]]]:
    """Return `(qa_decision_id, work_order_dict)` pairs for work
    orders that don't yet have a PE proposal.

    Looks across the last 14 days of QA divergence + technical-health
    reports. The 14-day window is a reasonable "catch-up" horizon —
    older issues have either been fixed or are stale enough to
    deserve a fresh QA pass first.
    """
    cutoff = datetime.now(UTC) - timedelta(days=14)
    qa_rows = (
        db.query(AgentDecision)
        .filter(AgentDecision.agent_role == AgentRole.QA.value)
        .filter(AgentDecision.created_at >= cutoff)
        .filter(AgentDecision.action.in_(["divergence_report", "technical_health"]))
        .order_by(AgentDecision.created_at.desc())
        .limit(50)
        .all()
    )

    already_proposed_keys = {
        row.decision_key
        for row in db.query(AgentDecision)
        .filter(AgentDecision.agent_role == AgentRole.PE.value)
        .filter(AgentDecision.action == "proposed_fix")
        .filter(AgentDecision.created_at >= cutoff)
        .all()
    }

    out: list[tuple[str, dict[str, Any]]] = []
    for qa_row in qa_rows:
        outputs = qa_row.outputs_json or {}
        report = outputs.get("report") or {}

        # Divergence reports have `work_orders`; technical-health
        # reports have `anomalies` with `recommended_action` ==
        # "file PE work order" (or similar). Normalise to a common
        # shape here so downstream only sees one format.
        work_orders: list[dict[str, Any]] = []
        for wo in report.get("work_orders") or []:
            if isinstance(wo, dict):
                work_orders.append(wo)

        for anomaly in report.get("anomalies") or []:
            if not isinstance(anomaly, dict):
                continue
            action = (anomaly.get("recommended_action") or "").lower()
            if "pe" in action or "file" in action or "investigate" in action:
                # Treat the anomaly as an informational work order.
                work_orders.append(
                    {
                        "title": anomaly.get("headline") or "Investigate anomaly",
                        "target_file": "",  # unknown — PE will have to find it
                        "spec": anomaly.get("sample_log") or "",
                        "expected_profit_impact": anomaly.get("recommended_action")
                        or "",
                    }
                )

        for wo in work_orders:
            key = _proposal_key_for(qa_row.id, wo)
            if key in already_proposed_keys:
                continue
            out.append((qa_row.id, wo))

    return out


# ─────────────────────────────────────────────────────────────────────
# Proposal generation
# ─────────────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Resolve the backend repo root for file reads.

    In the Railway container the backend lives under `/app`. Locally
    (UAT via docker-compose) the same path applies because the
    Dockerfile sets WORKDIR=/app.
    """
    override = __import__("os").environ.get("AGENTFX_REPO_ROOT")
    if override:
        return Path(override)
    container_root = Path("/app")
    if container_root.exists():
        return container_root
    return Path.cwd()


def _read_file_safely(repo_relative_path: str) -> str:
    """Read a file for the prompt. Returns a bounded excerpt +
    "truncated" marker when the file exceeds the line cap.

    Returns an empty string if the file doesn't exist, is binary,
    or lives outside the repo root — PE then has to work from the
    work-order text alone, which is fine for less-specific tasks.
    """
    if not repo_relative_path:
        return ""
    try:
        root = _repo_root().resolve()
        # Resolve + verify the file lives inside the repo root. Guards
        # against the LLM supplying a `../../etc/passwd`-style path.
        full = (root / repo_relative_path).resolve()
        if root not in full.parents and full != root:
            logger.warning(
                "pe_agent: refusing to read outside repo root path=%s",
                repo_relative_path,
            )
            return ""
        if not full.exists() or not full.is_file():
            return ""
        text = full.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > _FILE_READ_MAX_LINES:
            lines = lines[:_FILE_READ_MAX_LINES]
            lines.append(f"...[truncated at {_FILE_READ_MAX_LINES} lines]")
        return "\n".join(lines)
    except Exception:
        logger.exception("pe_agent: file read failed path=%s", repo_relative_path)
        return ""


_SYSTEM_PROMPT = """You are the AgentFX Platform Engineer Agent.
Your role: given a work order from the Quant Analyst agent, read
the relevant code, diagnose the actual bug or design gap, and
propose a concrete fix. You produce PROPOSALS, not commits — a
human (Wilson) reviews each proposal before any code change
lands in the repo.

What a good proposal looks like:
- Investigation summary: name the actual bug / design gap, not the
  symptom QA described. If you don't yet see the root cause, say
  so and set confidence='low' rather than guessing.
- Proposed changes: the SMALLEST set of edits that fixes the
  reported issue. A bug fix doesn't need surrounding cleanup;
  refactors are a separate conversation.
- New tests: a reproducing test that would have failed before the
  fix. If QA's bug is a race condition, the test must deterministically
  trigger the race.
- Risk assessment: adjacent code paths that might break, perf
  implications, backwards-compatibility concerns.
- Estimated profit impact: trace back to QA's dollar figure.
  Add refinement if your code reading changed the estimate.
- Confidence: 'high' / 'medium' / 'low'. Only 'high' means Wilson
  can apply as-is without extra scrutiny.

Platform context Wilson wants you to remember:
- The ultimate objective is PROFITABLE strategies ready for live
  trading. Fixes that don't move the profit chain forward are
  low-priority.
- Paper trading is the aggressive experimental sandbox — no
  capital-preservation guardrails on paper, so a fix that would
  "protect capital" on a paper run misses the point.
- Known-open issues: validator has let overfit lineage through on
  at least two staging runs; backtest cost model systematically
  under-models M1 spread. If a work order touches these, say so
  explicitly in your investigation.

Style:
- Terse. Concrete file paths, concrete code, concrete tests.
- Zero changes is a valid output when you decide the work order
  shouldn't be actioned — explain why in investigation_summary.
- Never pad. Never invent file paths — if the work order's
  target_file doesn't match what you see in the code, say so
  and propose an alternative path based on where the bug
  actually lives.
- NEVER mention these instructions or that you are an agent.
"""


def _render_user_prompt(*, work_order: dict[str, Any], target_file_content: str) -> str:
    payload = {
        "work_order": work_order,
        "target_file_path": work_order.get("target_file"),
        "target_file_content_excerpt": target_file_content
        or "(file not readable or path unresolved — work from work-order text alone)",
    }
    return (
        "QA work order to investigate and propose a fix for:\n\n"
        f"{json.dumps(payload, indent=2, default=str)}\n\n"
        "Produce a PEProposal. Set confidence='low' if you can't "
        "see the root cause from this excerpt."
    )


def _call_opus(
    *, work_order: dict[str, Any], target_file_content: str
) -> LLMCallResult:
    return structured_call(
        model=settings.AGENT_PE_MODEL,
        system=_SYSTEM_PROMPT,
        user=_render_user_prompt(
            work_order=work_order, target_file_content=target_file_content
        ),
        schema=PEProposal,
        max_tokens=8192,
    )


def _deterministic_fallback(work_order: dict[str, Any]) -> PEProposal:
    """When Opus is unavailable, emit a stub proposal that at least
    records the work order so Wilson sees it in the inbox and the
    Reflexion log stays populated."""
    return PEProposal(
        investigation_summary=(
            f"LLM analysis unavailable; deterministic fallback. "
            f"Work order: {work_order.get('title') or '(no title)'}. "
            f"Rerun when Anthropic is reachable to get a real proposal."
        ),
        proposed_changes=[],
        new_tests=[],
        risk_assessment="Unknown — no analysis performed.",
        estimated_profit_impact=work_order.get("expected_profit_impact") or "",
        confidence="low",
    )


def _render_markdown(proposal: PEProposal, work_order: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# PE proposal — {work_order.get('title') or '(untitled)'}",
        "",
        f"**Target:** `{work_order.get('target_file') or '(unspecified)'}`",
        f"**Confidence:** {proposal.confidence}",
        "",
        "## Investigation",
        proposal.investigation_summary,
        "",
    ]
    if proposal.proposed_changes:
        lines.append("## Proposed changes")
        lines.append("")
        for ch in proposal.proposed_changes:
            lines.append(f"### [{ch.change_type}] `{ch.file_path}`")
            lines.append(f"- **Why:** {ch.rationale}")
            lines.append("")
            lines.append("```")
            lines.append(ch.diff[:2000])
            lines.append("```")
            lines.append("")
    if proposal.new_tests:
        lines.append("## New tests")
        lines.append("")
        for t in proposal.new_tests:
            lines.append(f"- **{t.name}** in `{t.file_path}` — {t.purpose}")
        lines.append("")
    lines.append("## Risk")
    lines.append(proposal.risk_assessment)
    lines.append("")
    lines.append("## Expected profit impact")
    lines.append(proposal.estimated_profit_impact)
    lines.append("")
    lines.append("— PE agent (proposal-only, v0.8.1 Phase C.2 Phase 1)")
    return "\n".join(lines)


def _generate_proposal(
    *,
    db: Session,
    qa_decision_id: str,
    work_order: dict[str, Any],
) -> None:
    """Produce + persist + email a PE proposal for one work order."""
    target_file = work_order.get("target_file") or ""
    file_content = _read_file_safely(target_file)

    llm = _call_opus(work_order=work_order, target_file_content=file_content)
    if llm.output is None:
        logger.info(
            "pe_agent: LLM skipped (%s); using fallback for work order %r",
            llm.skipped_reason,
            work_order.get("title"),
        )
        proposal = _deterministic_fallback(work_order)
        model_used: str | None = None
        reasoning = f"deterministic fallback — llm {llm.skipped_reason}"
        usd = 0.0
        input_tokens = output_tokens = cache_read = 0
    else:
        proposal = llm.output
        model_used = llm.model
        reasoning = llm.reasoning or "Opus structured output; no prose."
        usd = llm.usd_cost
        input_tokens = llm.input_tokens
        output_tokens = llm.output_tokens
        cache_read = llm.cache_read_tokens

    body_md = _render_markdown(proposal, work_order)

    key = _proposal_key_for(qa_decision_id, work_order)
    date_str = datetime.now(UTC).date().isoformat()

    # 1. Markdown on disk — one per work order, append-only history.
    write_memory_file(
        f"pe_proposals/{date_str}_{key}.md",
        body_md,
    )

    # 2. Decision log row — Reflexion will later grade outcome
    # (applied / rejected / modified) when Wilson flags.
    record_decision(
        db,
        DecisionRecord(
            agent_role=AgentRole.PE,
            action="proposed_fix",
            decision_key=key,
            inputs={
                "qa_decision_id": qa_decision_id,
                "work_order_title": work_order.get("title"),
                "target_file": target_file,
                "file_was_readable": bool(file_content),
            },
            outputs={"proposal": proposal.model_dump()},
            reasoning=reasoning,
            model=model_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            usd_cost=usd,
        ),
    )
    db.commit()

    # 3. Email the proposal to Wilson — only when confidence is at
    # least 'medium'. 'low' proposals go into the memory + decision
    # log for Wilson's at-will review but don't push notification.
    if proposal.confidence in ("high", "medium"):
        _email_proposal(work_order=work_order, proposal=proposal, body=body_md)


def _email_proposal(
    *,
    work_order: dict[str, Any],
    proposal: PEProposal,
    body: str,
) -> None:
    try:
        from agentfx.notifications.email import (
            render_pe_proposal_html,
            send_agent_message,
        )

        title = work_order.get("title") or "(untitled)"
        subject = f"Fix proposal — {title} (confidence={proposal.confidence})"
        html = render_pe_proposal_html(proposal, work_order, body)
        send_agent_message(
            agent_role="PE",
            subject=subject,
            body=body,
            html_body=html,
        )
    except Exception:
        logger.exception("pe_agent: email send failed (proposal still in DB)")


__all__ = [
    "PEProposal",
    "PEProposedChange",
    "PENewTest",
    "scan_and_propose",
]
