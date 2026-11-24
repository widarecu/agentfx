import asyncio
from unittest.mock import patch

import pytest
from agentfx.api import models, schemas
from agentfx.api.services.evolution import create_run
from agentfx.api.workers import candle_ingestion, evolution_runner, paper_runner
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_startup_recovery_leaves_runs_owned_by_another_backend(db_session, test_engine):
    paper = models.Run(
        id="run_backend_overlap",
        pair="EURUSD",
        environment=models.RunEnvironment.PAPER,
        status=models.RunStatus.RUNNING,
        parameters={},
        process_pid=123,
    )
    db_session.add(paper)
    evolution = create_run(db_session, schemas.EvolutionRunCreate(instrument="EUR_USD"))
    evolution.status = models.EvolutionRunStatus.RUNNING
    evolution.process_pid = 124
    db_session.commit()
    paper_id, evolution_id = paper.id, evolution.id

    with (
        patch.object(paper_runner, "schedule_paper_run") as schedule_paper,
        patch.object(evolution_runner, "schedule_run") as schedule_evolution,
    ):
        # This separate PostgreSQL session stands in for live child processes.
        with test_engine.begin() as incumbent:
            for key in (
                f"agentfx:paper:{paper_id}",
                f"agentfx:evolution:{evolution_id}",
            ):
                incumbent.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": key},
                )

            paper_runner.recover_orphaned_paper_runs(db=db_session)
            evolution_runner.recover_queued_evolution_runs(db=db_session)

            schedule_paper.assert_not_called()
            schedule_evolution.assert_not_called()
            db_session.refresh(paper)
            db_session.refresh(evolution)
            assert (paper.status, paper.process_pid) == (models.RunStatus.RUNNING, 123)
            assert (evolution.status, evolution.process_pid) == (
                models.EvolutionRunStatus.RUNNING,
                124,
            )

            deferred_paper, deferred_evolution = set(), set()
            paper_runner.recover_orphaned_paper_runs(
                db=db_session, deferred=deferred_paper
            )
            evolution_runner.recover_queued_evolution_runs(
                db=db_session, deferred=deferred_evolution
            )
            assert deferred_paper == {paper_id}
            assert deferred_evolution == {evolution_id}

        paper_runner.recover_orphaned_paper_runs(
            db=db_session, deferred=deferred_paper, retry_only=True
        )
        evolution_runner.recover_queued_evolution_runs(
            db=db_session, deferred=deferred_evolution, retry_only=True
        )
        schedule_paper.assert_called_once_with(paper_id)
        schedule_evolution.assert_called_once_with(evolution_id)
        assert not deferred_paper and not deferred_evolution


@pytest.mark.parametrize("kind", ["paper", "evolution"])
def test_child_holds_run_lock_while_executing(kind, test_engine, monkeypatch):
    from agentfx.api.core import database
    from agentfx.services import paper_runner as paper_service_runner

    run_id = "run_child_owner"
    lock = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")
    key = {"key": f"agentfx:{kind}:{run_id}"}
    calls = []

    def verify_exclusive_owner(*args, **kwargs):
        with test_engine.begin() as challenger:
            assert not challenger.scalar(lock, key)
        calls.append(run_id)

    monkeypatch.setattr(database, "engine", test_engine)
    if kind == "paper":
        monkeypatch.setattr(
            paper_service_runner, "run_paper_run", verify_exclusive_owner
        )
        paper_runner._subprocess_entry(run_id)
    else:
        monkeypatch.setattr(
            evolution_runner, "_run_owned_evolution", verify_exclusive_owner
        )
        evolution_runner._subprocess_entry(run_id)

    assert calls == [run_id]
    with test_engine.begin() as challenger:
        assert challenger.scalar(lock, key)


@pytest.mark.asyncio
async def test_candle_ingestion_waits_for_the_existing_owner(test_engine, monkeypatch):
    from agentfx.api.core import database

    started = asyncio.Event()
    stop = asyncio.Event()

    async def fake_ingestion():
        started.set()
        await stop.wait()

    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(candle_ingestion, "ingestion_loop", fake_ingestion)

    standby = None
    try:
        with test_engine.begin() as incumbent:
            incumbent.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtextextended('agentfx:candle-ingestion', 0))"
                )
            )
            standby = asyncio.create_task(
                candle_ingestion._ingest_as_owner(stop, retry_seconds=0.01)
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(started.wait(), timeout=0.05)
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        stop.set()
        if standby is not None:
            await asyncio.wait_for(standby, timeout=1)
