"""Shared memory substrate for the v0.8.x Agent Fleet.

Two surfaces, used together:

1. **`agent_decisions` DB table.** Append-only log of every decision
   each agent made (see `agentfx.api.models.agent_decision`). This
   is the Reflexion dataset — later re-queried to grade past calls.

2. **`agent_memory/` filesystem directory.** Markdown files per
   topic (`scoreboard.md`, `cost_model_findings.md`, per-run
   divergence reports, etc.). Agents read these as prompt context
   on their next call.

Both are deliberately simple. The point is observability — Wilson
can `grep agent_memory/` and `psql -c "select ... from
agent_decisions"` and understand what every agent did and why.

Why not just the DB? Markdown is what the LLMs produce naturally
(prose + tables + bullet lists). Shoe-horning that into relational
columns loses nuance and makes human review harder.

Why not just files? Reflexion needs structured queries — "all PM
decisions this week," "all QA 'cost_model' classifications graded
`bad`." That's SQL territory.

Both. Each stays shaped for its real consumer.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from agentfx.api.models.agent_decision import AgentDecision, AgentRole

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Markdown directory layout
# ─────────────────────────────────────────────────────────────────────


def agent_memory_root() -> Path:
    """Return the `agent_memory/` directory root as a Path.

    Resolution order:
    1. `AGENT_MEMORY_ROOT` env var — explicit override, respected
       first so tests + Railway can relocate it without a code
       change.
    2. `./agent_memory` relative to CWD — the development default
       (CWD is the repo root when the backend is run via `make
       docker-up` or local uvicorn).

    The directory is created if missing. Markdown files are
    human-authored by agents + Wilson; nothing enforces a schema.
    """
    env = os.environ.get("AGENT_MEMORY_ROOT")
    root = Path(env) if env else Path.cwd() / "agent_memory"
    root.mkdir(parents=True, exist_ok=True)
    return root


def read_memory_file(relative_path: str) -> str:
    """Read a markdown memory file; return empty string if missing.

    Agents use this to inject prior context into prompts. Missing is
    not an error — a fresh deployment has no memory yet.
    """
    path = agent_memory_root() / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning("agent_memory: read failed path=%s err=%s", relative_path, exc)
        return ""


def write_memory_file(relative_path: str, content: str) -> None:
    """Overwrite a markdown memory file with `content`.

    Creates parent directories as needed. Writes atomically via a
    temporary file + rename, so a crash mid-write doesn't leave the
    file half-populated — agents that read mid-write will see either
    the old version or the new one.
    """
    root = agent_memory_root()
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("agent_memory: write failed path=%s err=%s", relative_path, exc)
        with suppress_oserror(tmp.unlink):
            pass


def append_memory_file(relative_path: str, content: str) -> None:
    """Append to a markdown memory file (creating it if missing).

    Non-atomic — fine for an append-only decision log where the
    worst case of an interrupted write is a truncated last entry.
    """
    root = agent_memory_root()
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        logger.warning("agent_memory: append failed path=%s err=%s", relative_path, exc)


# Small helper so write-failure cleanup doesn't paper over bugs.
class suppress_oserror:  # noqa: N801 - stdlib ContextDecorator style
    def __init__(self, fn):
        self._fn = fn

    def __enter__(self):
        try:
            self._fn()
        except OSError:
            pass

    def __exit__(self, *exc):
        return False


# ─────────────────────────────────────────────────────────────────────
# DB-side decision log
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionRecord:
    """What the orchestrator records about one agent call."""

    agent_role: AgentRole
    action: str
    decision_key: str
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    reasoning: str | None = None
    model: str | None = None
    input_tokens: float | None = None
    output_tokens: float | None = None
    cache_read_tokens: float | None = None
    usd_cost: float | None = None


def record_decision(db: Session, rec: DecisionRecord) -> AgentDecision:
    """Persist a decision row and return it.

    Caller owns the transaction — this adds + flushes but does not
    commit, so the orchestrator can batch multiple writes (e.g. the
    morning brief plus its sub-tasks) in one commit.
    """
    row = AgentDecision(
        id=uuid.uuid4().hex,
        agent_role=rec.agent_role.value,
        action=rec.action,
        decision_key=rec.decision_key,
        inputs_json=rec.inputs,
        outputs_json=rec.outputs,
        reasoning_text=rec.reasoning,
        model=rec.model,
        input_tokens=rec.input_tokens,
        output_tokens=rec.output_tokens,
        cache_read_tokens=rec.cache_read_tokens,
        usd_cost=rec.usd_cost,
    )
    db.add(row)
    db.flush()
    return row


def record_outcome(
    db: Session,
    *,
    decision_id: str,
    outcome: dict[str, Any],
    grade: str,
) -> None:
    """Fill in the Reflexion outcome fields on an existing row.

    Agents call this LATER when the real-world result of an earlier
    decision is known (e.g. "the paper deployment PM approved on
    Monday has been profitable for 7 days"). Does not touch any
    other field on the row — the original reasoning stays frozen.

    `grade` is one of: "good", "bad", "mixed", "no_signal", "pending".
    Pending means the outcome window is still open; the row is read
    by Reflexion but excluded from "lessons learned" summaries.
    """
    row = db.get(AgentDecision, decision_id)
    if row is None:
        logger.warning(
            "agent_decisions: outcome record skipped — decision not found id=%s",
            decision_id,
        )
        return
    row.outcome_json = outcome
    row.outcome_recorded_at = datetime.now(UTC)
    row.outcome_grade = grade
    db.flush()


def recent_decisions(
    db: Session,
    *,
    agent_role: AgentRole | None = None,
    limit: int = 20,
) -> list[AgentDecision]:
    """Return the N most recent decisions, newest first.

    Used by Reflexion-flavoured prompts to inject prior context.
    If `agent_role` is given, filters to that specialist; otherwise
    returns the fleet's full timeline.
    """
    q = db.query(AgentDecision).order_by(AgentDecision.created_at.desc())
    if agent_role is not None:
        q = q.filter(AgentDecision.agent_role == agent_role.value)
    return q.limit(limit).all()
