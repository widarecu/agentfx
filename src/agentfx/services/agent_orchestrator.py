"""v0.8.x Agent Fleet orchestrator.

Scope at this phase (v0.8.0 Phase A):

- Register a FastAPI lifespan hook that spawns one asyncio task per
  active agent (currently only the Portfolio Manager daily brief).
- Enforce the daily USD cap defined in `settings.AGENT_FLEET_DAILY_USD_CAP`
  by consulting today's cumulative `usd_cost` across `agent_decisions`
  before dispatching the next run.
- Alert on monthly spend crossing `AGENT_FLEET_MONTHLY_USD_ALERT`
  (one-shot email, idempotent via `agent_memory/_alerts/`).

Everything else — QA, PE workspaces, RA, scheduling tool, auto-
approve — comes in later phases per the plan.

Why a single orchestrator and not one task per agent file?
Observability. One scheduler loop means one place to see "which
agents are armed, when do they next fire, what did they last
spend." Spreads out quickly as the fleet grows otherwise.

Why asyncio + sleep rather than APScheduler?
Zero new dependencies. The ingestion worker already uses the same
pattern (loop with sleep inside the FastAPI lifespan — see
`candle_ingestion.py`). Keeps v0.8.0 Phase A at the smallest
possible blast radius; APScheduler can land later if real cron
semantics are needed (e.g. "exactly 09:00 UTC regardless of
drift").
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from agentfx.agents.fleet.log_buffer import get_global_buffer
from agentfx.agents.fleet.memory import agent_memory_root, write_memory_file
from agentfx.api.core.config import settings
from agentfx.api.core.database import SessionLocal
from agentfx.api.models.agent_decision import AgentDecision, AgentRole

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Budget accounting
# ─────────────────────────────────────────────────────────────────────


def _usd_spent_on(db: Session, *, day: date) -> float:
    """Sum of `usd_cost` across all agent_decisions created on `day`."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    total = (
        db.query(func.coalesce(func.sum(AgentDecision.usd_cost), 0.0))
        .filter(AgentDecision.created_at >= start)
        .filter(AgentDecision.created_at < end)
        .scalar()
    )
    return float(total or 0.0)


def _usd_spent_month_to_date(db: Session, *, today: date) -> float:
    """Sum of `usd_cost` across all agent_decisions this calendar month."""
    start = datetime(today.year, today.month, 1, tzinfo=UTC)
    total = (
        db.query(func.coalesce(func.sum(AgentDecision.usd_cost), 0.0))
        .filter(AgentDecision.created_at >= start)
        .scalar()
    )
    return float(total or 0.0)


def is_within_daily_budget(db: Session, *, today: date | None = None) -> bool:
    """Return True iff today's cumulative fleet spend is under the cap.

    The cap is `settings.AGENT_FLEET_DAILY_USD_CAP`. When the cap is
    exceeded, the orchestrator skips the next scheduled run and the
    caller is responsible for logging + alerting.
    """
    today = today or datetime.now(UTC).date()
    spent = _usd_spent_on(db, day=today)
    return spent < settings.AGENT_FLEET_DAILY_USD_CAP


def should_alert_monthly_overspend(db: Session, *, today: date | None = None) -> bool:
    """Return True iff month-to-date spend crossed the alert threshold
    AND the one-shot sentinel for this month has not been written.

    Writes the sentinel on True so the next call returns False — the
    alert is one-shot per calendar month.
    """
    today = today or datetime.now(UTC).date()
    spent = _usd_spent_month_to_date(db, today=today)
    if spent < settings.AGENT_FLEET_MONTHLY_USD_ALERT:
        return False

    sentinel_rel = f"_alerts/monthly_overspend_{today.strftime('%Y-%m')}.marker"
    sentinel_abs = agent_memory_root() / sentinel_rel
    if sentinel_abs.exists():
        return False

    # Fire — write sentinel so this won't fire again this month.
    write_memory_file(
        sentinel_rel,
        f"Fired at {datetime.now(UTC).isoformat()} UTC after month-to-date "
        f"spend ${spent:.2f} crossed ${settings.AGENT_FLEET_MONTHLY_USD_ALERT:.2f}.\n",
    )
    return True


# ─────────────────────────────────────────────────────────────────────
# Lifespan task
# ─────────────────────────────────────────────────────────────────────


async def _pm_daily_loop() -> None:
    """Fire the Portfolio Manager daily brief once per 24 hours.

    Simple sleep loop — on wake, checks whether today's brief has
    been written yet, and if not, invokes the PM. On error, logs and
    continues (the alert goes out to Wilson via the standard
    notification path when PM email send fails).

    Lazy imports keep the orchestrator module importable without the
    full fleet surface loaded (useful for the no-op `register`
    path when `AGENT_FLEET_ENABLED=False`).
    """
    from agentfx.agents.fleet.portfolio_manager import run_morning_brief_if_due

    logger.info("agent_orchestrator: PM daily loop armed")
    while True:
        try:
            with SessionLocal() as db:
                if not is_within_daily_budget(db):
                    logger.warning(
                        "agent_orchestrator: PM run skipped — daily budget "
                        "exceeded for %s",
                        datetime.now(UTC).date(),
                    )
                else:
                    run_morning_brief_if_due(db=db)
        except Exception:  # noqa: BLE001 — the orchestrator must never crash
            logger.exception("agent_orchestrator: PM loop iteration failed")

        # Sleep ~1h; wake up to re-check at least that often. The PM
        # itself handles "is it 09:00 UTC yet" — this is just a
        # polling cadence for the time check.
        await asyncio.sleep(60 * 60)


# PE scan cadence — daily is enough since Opus 5 is the pricey model
# and QA work orders don't accumulate faster than once per day in
# practice. One PE scan per day with a hard 3-proposal cap keeps
# Opus 5 spend bounded (~$0.75/day worst case).
_PE_SCAN_SECONDS = 60 * 60 * 24


async def _pe_scan_loop() -> None:
    """Daily scan: find unprocessed QA work orders → generate PE
    fix proposals via Opus 5. Never raises."""
    from agentfx.agents.fleet.platform_engineer import scan_and_propose

    logger.info(
        "agent_orchestrator: PE scan loop armed (cadence=%ds)",
        _PE_SCAN_SECONDS,
    )
    while True:
        try:
            with SessionLocal() as db:
                if not is_within_daily_budget(db):
                    logger.warning(
                        "agent_orchestrator: PE scan skipped — daily "
                        "budget exceeded for %s",
                        datetime.now(UTC).date(),
                    )
                else:
                    produced = scan_and_propose(db=db)
                    if produced:
                        logger.info(
                            "agent_orchestrator: PE produced %d proposal(s)",
                            produced,
                        )
        except Exception:
            logger.exception("agent_orchestrator: PE scan iteration failed")

        await asyncio.sleep(_PE_SCAN_SECONDS)


# QA scan cadence. 30 min balances two concerns:
# - A new fill on paper should be analysed in roughly the same hour
#   it happens; 30 min keeps the signal fresh.
# - Each scan is cheap (DB-only count queries) until it finds a
#   candidate; too-frequent scans still cost near-nothing.
_QA_SCAN_SECONDS = 60 * 30


async def _qa_scan_loop() -> None:
    """Scan paper runs for QA divergence reports every 30 minutes,
    AND fire the daily technical-health scan once per day.

    QA runs event-triggered — the scan finds paper runs with
    meaningful new activity since their last report (or any
    terminal stop_reason within the day) and dispatches a Sonnet 5
    call per run. The tech-health scan is independent of paper
    activity (the two feed different Reflexion grades) and fires
    at most once per calendar day; it analyses the backend's
    WARNING+ log buffer for anomalies the UI doesn't surface.
    """
    from agentfx.agents.fleet.quant_analyst import (
        scan_and_run_reports,
        scan_for_technical_anomalies,
    )

    logger.info(
        "agent_orchestrator: QA scan loop armed (cadence=%ds)", _QA_SCAN_SECONDS
    )
    while True:
        try:
            with SessionLocal() as db:
                if not is_within_daily_budget(db):
                    logger.warning(
                        "agent_orchestrator: QA scan skipped — daily budget "
                        "exceeded for %s",
                        datetime.now(UTC).date(),
                    )
                else:
                    produced = scan_and_run_reports(db=db)
                    if produced:
                        logger.info(
                            "agent_orchestrator: QA produced %d divergence report(s)",
                            produced,
                        )
                    # Tech-health scan is idempotent per day; this
                    # call is a no-op once the day's report exists.
                    if scan_for_technical_anomalies(db=db):
                        logger.info(
                            "agent_orchestrator: QA produced tech-health report"
                        )
        except Exception:
            logger.exception("agent_orchestrator: QA scan iteration failed")

        await asyncio.sleep(_QA_SCAN_SECONDS)


# Set from `register_lifespan_tasks`; retained so tests can inspect
# or cancel the running tasks explicitly.
_active_tasks: list[asyncio.Task[Any]] = []


def register_lifespan_tasks() -> list[asyncio.Task[Any]]:
    """Spawn the active agent lifespan tasks. Called from FastAPI
    lifespan startup only when `settings.AGENT_FLEET_ENABLED` is True.

    Returns the list of asyncio Tasks so the caller can cancel them
    on shutdown. If the feature flag is False, the function is a
    no-op and returns an empty list (NOT raise — the import must
    stay cheap for every environment, especially tests).
    """
    global _active_tasks
    if not settings.AGENT_FLEET_ENABLED:
        logger.info("agent_orchestrator: AGENT_FLEET_ENABLED=False — fleet is off")
        return []

    logger.info(
        "agent_orchestrator: starting fleet (PM=%s, QA=%s, PE=%s, daily_cap=$%.2f)",
        settings.AGENT_PM_MODEL,
        settings.AGENT_QA_MODEL,
        settings.AGENT_PE_MODEL,
        settings.AGENT_FLEET_DAILY_USD_CAP,
    )

    # Attach the process-wide log buffer BEFORE the agent loops start
    # so QA sees anything the loops themselves emit on their first
    # iteration. Idempotent — safe to call across lifespan restarts.
    get_global_buffer().attach_global_handler()
    logger.info("agent_orchestrator: log capture buffer attached")

    _active_tasks = [
        asyncio.create_task(_pm_daily_loop(), name="fleet_pm_daily"),
        asyncio.create_task(_qa_scan_loop(), name="fleet_qa_scan"),
        asyncio.create_task(_pe_scan_loop(), name="fleet_pe_scan"),
    ]
    return _active_tasks


async def shutdown_lifespan_tasks() -> None:
    """Cancel + await every task spawned by `register_lifespan_tasks`."""
    for task in _active_tasks:
        task.cancel()
    for task in _active_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — shutdown must be best-effort
            logger.exception(
                "agent_orchestrator: task %s raised on shutdown", task.get_name()
            )
    _active_tasks.clear()


__all__ = [
    "AgentRole",
    "is_within_daily_budget",
    "should_alert_monthly_overspend",
    "register_lifespan_tasks",
    "shutdown_lifespan_tasks",
]
