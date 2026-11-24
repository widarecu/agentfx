import asyncio
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from agentfx.api import models, schemas
from agentfx.api.services.evolution import create_run
from agentfx.api.workers import candle_ingestion, evolution_runner, paper_runner
from agentfx.api.workers.ownership import worker_lock
from sqlalchemy import event, text
from sqlalchemy.orm import Session

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
def test_recovery_tracks_a_deferred_run_starting_during_the_scan(
    kind, db_session, test_engine, monkeypatch
):
    if kind == "paper":
        run = models.Run(
            id="run_deferred_start",
            pair="EURUSD",
            environment=models.RunEnvironment.PAPER,
            status=models.RunStatus.APPROVED,
            parameters={},
        )
        db_session.add(run)
        recover = paper_runner.recover_orphaned_paper_runs
        worker, schedule_attr = paper_runner, "schedule_paper_run"
        running = models.RunStatus.RUNNING
    else:
        run = create_run(db_session, schemas.EvolutionRunCreate(instrument="EUR_USD"))
        recover = evolution_runner.recover_queued_evolution_runs
        worker, schedule_attr = evolution_runner, "schedule_run"
        running = models.EvolutionRunStatus.RUNNING
    db_session.flush()
    run_id = run.id
    deferred = {run_id}
    connection = db_session.connection()
    table = type(run).__table__
    started = False

    @event.listens_for(connection, "after_cursor_execute")
    def start_after_first_select(conn, cursor, statement, *args):
        nonlocal started
        if (
            not started
            and statement.startswith("SELECT")
            and f"FROM {table.name}" in statement
        ):
            started = True
            conn.execute(
                table.update().where(table.c.id == run_id).values(status=running)
            )

    scheduled = []
    monkeypatch.setattr(worker, schedule_attr, scheduled.append)
    try:
        with test_engine.begin() as incumbent:
            incumbent.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"agentfx:{kind}:{run_id}"},
            )
            assert recover(db=db_session, deferred=deferred, retry_only=True) == 0
            assert started and deferred == {run_id}
        assert recover(db=db_session, deferred=deferred, retry_only=True) == 1
        assert not deferred and scheduled == [run_id]
    finally:
        event.remove(connection, "after_cursor_execute", start_after_first_select)


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


def test_owner_session_cannot_query_after_reconnecting(test_engine):
    key = "agentfx:paper:run_disconnected_owner"
    with worker_lock(test_engine, key) as (owned, connection):
        assert owned
        with Session(bind=connection) as db:
            db.scalar(text("SELECT 1"))
            connection.invalidate()
            db.rollback()

            with worker_lock(test_engine, key) as (successor_owned, _):
                assert successor_owned
                with pytest.raises(
                    RuntimeError, match="Database session lost worker lock"
                ):
                    db.scalar(text("SELECT 1"))


def test_resumed_paper_child_waits_for_halted_owner(test_engine, monkeypatch):
    from agentfx.api.core import database
    from agentfx.services import paper_runner as paper_service_runner

    run_id = "run_resume_during_halt"
    with Session(test_engine) as db:
        db.add(
            models.Run(
                id=run_id,
                pair="EURUSD",
                environment=models.RunEnvironment.PAPER,
                status=models.RunStatus.APPROVED,
                parameters={},
            )
        )
        db.commit()
    monkeypatch.setattr(database, "engine", test_engine)
    try:
        with (
            ExitStack() as old_runner,
            patch.object(paper_service_runner, "run_paper_run") as execute,
        ):
            owned, _ = old_runner.enter_context(
                worker_lock(test_engine, f"agentfx:paper:{run_id}")
            )
            assert owned

            def finish_halt_notification(_seconds):
                execute.assert_not_called()
                old_runner.close()

            monkeypatch.setattr(paper_runner, "sleep", finish_halt_notification)
            paper_runner._subprocess_entry(run_id)
            execute.assert_called_once()
            assert execute.call_args.args == (run_id,)
    finally:
        with Session(test_engine) as db:
            db.delete(db.get(models.Run, run_id))
            db.commit()


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
