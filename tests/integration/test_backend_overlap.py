import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from agentfx.api import models, schemas
from agentfx.api.services.evolution import (
    EvolutionRunStopped,
    SqlAlchemyPersister,
    create_run,
    stop_run,
)
from agentfx.api.workers import candle_ingestion, evolution_runner, paper_runner
from agentfx.api.workers.ownership import worker_lock
from sqlalchemy import create_engine, event, text
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


def test_remote_evolution_stop_cannot_be_overwritten_by_the_owner(test_engine):
    with Session(test_engine) as owner, Session(test_engine) as api:
        run = create_run(owner, schemas.EvolutionRunCreate(instrument="EUR_USD"))
        run_id = run.id
        try:
            persister = SqlAlchemyPersister(owner)
            persister.update_run(run_id, status="running")
            assert run.status == models.EvolutionRunStatus.RUNNING
            persister.check_stop(run_id)

            stopped = stop_run(api, run_id)
            assert stopped.status == models.EvolutionRunStatus.STOPPED
            with pytest.raises(EvolutionRunStopped):
                persister.check_stop(run_id)
            with pytest.raises(EvolutionRunStopped):
                persister.update_run(
                    run_id, status="completed", stop_reason="max_generations"
                )
            owner.rollback()

            api.refresh(stopped)
            assert stopped.status == models.EvolutionRunStatus.STOPPED
            assert stopped.stop_reason == "manual_stop"
        finally:
            owner.rollback()
            api.delete(api.get(models.EvolutionRun, run_id))
            api.commit()


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


_REPO = Path(__file__).resolve().parents[2]


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _wait_for(path, predicate, timeout=60.0):
    deadline = time.monotonic() + timeout
    while True:
        events = _events(path)
        if predicate(events):
            return events
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out; events so far: {events}")
        time.sleep(0.2)


@pytest.fixture
def overlap_database(test_engine):
    from agentfx.api.core.database import Base

    name = f"agentfx_overlap_{uuid.uuid4().hex[:12]}"
    admin = test_engine.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = test_engine.url.set(database=name)
    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
        yield engine, url.render_as_string(hide_password=False)
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')


def test_backend_processes_hand_live_work_to_exactly_one_successor(
    overlap_database, tmp_path
):
    """Three real backend processes overlap as on a deploy: the first owns a
    live paper run, an evolution run and the candle writer through spawned
    workers; two successors start while it lives and must leave all three
    alone; once the first backend dies, exactly one worker resumes each."""
    engine, database_url = overlap_database
    with Session(engine) as db:
        db.add(
            models.Run(
                id="run_overlap_paper",
                pair="EURUSD",
                environment=models.RunEnvironment.PAPER,
                status=models.RunStatus.APPROVED,
                parameters={},
            )
        )
        evolution_id = create_run(
            db, schemas.EvolutionRunCreate(instrument="EUR_USD")
        ).id
        db.commit()
    paper_id = "run_overlap_paper"

    events_path = tmp_path / "events.jsonl"
    events_path.touch()
    backends = {}

    def start(name):
        env = {
            **os.environ,
            "ENVIRONMENT": "test",
            "TESTING": "true",
            "DATABASE_URL": database_url,
            "TEST_DATABASE_URL": database_url,
            "INGESTION_ENABLED": "true",
            "OANDA_API_KEY": "overlap-test-key",
            "AGENT_FLEET_ENABLED": "false",
            "PYTHONPATH": os.pathsep.join([str(_REPO / "src"), str(_REPO)]),
            "AFX_OVERLAP_BACKEND": name,
            "AFX_OVERLAP_EVENTS": str(events_path),
        }
        workdir = tmp_path / name
        workdir.mkdir()
        backends[name] = subprocess.Popen(
            [sys.executable, "-m", "tests.integration.overlap_backend"],
            cwd=workdir,
            env=env,
            start_new_session=True,
        )

    def stop(name):
        process = backends.pop(name)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)

    def starts(events, *, backends=None):
        return [
            event
            for event in events
            if event["kind"] in ("worker", "ingestion")
            and (backends is None or event["backend"] in backends)
        ]

    def owners():
        with Session(engine) as db:
            paper = db.get(models.Run, paper_id)
            evolution = db.get(models.EvolutionRun, evolution_id)
            return {
                paper_id: (paper.status, paper.process_pid),
                evolution_id: (evolution.status, evolution.process_pid),
            }

    try:
        start("old")
        events = _wait_for(events_path, lambda e: len(starts(e)) == 3)
        old_workers = {
            event["run_id"]: event["pid"]
            for event in events
            if event["kind"] == "worker"
        }
        assert set(old_workers) == {paper_id, evolution_id}
        assert owners() == {
            paper_id: (models.RunStatus.RUNNING, old_workers[paper_id]),
            evolution_id: (
                models.EvolutionRunStatus.RUNNING,
                old_workers[evolution_id],
            ),
        }

        start("new_a")
        start("new_b")

        def successors_deferred_twice(events):
            for backend in ("new_a", "new_b"):
                for recover, run_id in (
                    ("recover_orphaned_paper_runs", paper_id),
                    ("recover_queued_evolution_runs", evolution_id),
                ):
                    passes = [
                        event
                        for event in events
                        if event["backend"] == backend and event["kind"] == recover
                    ]
                    if len(passes) < 2:
                        return False
                    assert all(
                        event["scheduled"] == 0 and event["deferred"] == [run_id]
                        for event in passes
                    ), passes
            return True

        events = _wait_for(events_path, successors_deferred_twice)
        assert starts(events) == starts(events, backends={"old"})
        assert len(starts(events)) == 3
        assert owners() == {
            paper_id: (models.RunStatus.RUNNING, old_workers[paper_id]),
            evolution_id: (
                models.EvolutionRunStatus.RUNNING,
                old_workers[evolution_id],
            ),
        }

        stop("old")
        successors = {"new_a", "new_b"}
        events = _wait_for(
            events_path, lambda e: len(starts(e, backends=successors)) == 3
        )
        # Let both successors finish another recovery and ingestion retry.
        time.sleep(7)
        events = _events(events_path)
        taken_over = starts(events, backends=successors)
        assert sorted(event.get("run_id", "ingestion") for event in taken_over) == (
            sorted([paper_id, evolution_id, "ingestion"])
        )
        new_workers = {
            event["run_id"]: event["pid"]
            for event in taken_over
            if event["kind"] == "worker"
        }
        assert owners() == {
            paper_id: (models.RunStatus.RUNNING, new_workers[paper_id]),
            evolution_id: (
                models.EvolutionRunStatus.RUNNING,
                new_workers[evolution_id],
            ),
        }
    finally:
        for name in list(backends):
            stop(name)
