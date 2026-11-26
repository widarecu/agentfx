"""One AgentFX backend process for the overlap test in test_backend_overlap.py.

Runs the real FastAPI lifespan, so startup recovery, real spawned run
workers and candle-ingestion ownership behave as on a deploy. Only the
work an owner does is replaced: a paper or evolution worker marks its run
RUNNING and then idles, and the candle writer idles. Each start is
appended to the JSON-lines file named by AFX_OVERLAP_EVENTS.
"""

import asyncio
import json
import os
import signal
import time


def record(kind: str, **fields) -> None:
    line = json.dumps(
        {"backend": os.environ["AFX_OVERLAP_BACKEND"], "kind": kind, **fields}
    )
    fd = os.open(os.environ["AFX_OVERLAP_EVENTS"], os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)


def _own_run(owner_connection, model, run_id: str, ready, running) -> None:
    from sqlalchemy.orm import Session

    with Session(bind=owner_connection) as db:
        run = db.get(model, run_id)
        if run is None or run.status != ready:
            return
        run.status = running
        run.process_pid = os.getpid()
        db.commit()
    record("worker", run_id=run_id, pid=os.getpid())
    while True:
        time.sleep(60)


def paper_worker(run_id: str) -> None:
    from agentfx.api.models import Run, RunStatus
    from agentfx.api.workers import paper_runner
    from agentfx.services import paper_runner as paper_service

    def run_paper_run(run_id, *, owner_connection=None):
        _own_run(owner_connection, Run, run_id, RunStatus.APPROVED, RunStatus.RUNNING)

    paper_service.run_paper_run = run_paper_run
    paper_runner._subprocess_entry(run_id)


def evolution_worker(run_id: str) -> None:
    from agentfx.api.models import EvolutionRun, EvolutionRunStatus
    from agentfx.api.workers import evolution_runner

    def run_owned_evolution(run_id, owner_connection):
        _own_run(
            owner_connection,
            EvolutionRun,
            run_id,
            EvolutionRunStatus.QUEUED,
            EvolutionRunStatus.RUNNING,
        )

    evolution_runner._run_owned_evolution = run_owned_evolution
    evolution_runner._subprocess_entry(run_id)


def _recording(recover):
    def wrapper(*, deferred, retry_only):
        scheduled = recover(deferred=deferred, retry_only=retry_only)
        record(recover.__name__, scheduled=scheduled, deferred=sorted(deferred))
        return scheduled

    wrapper.__name__ = recover.__name__
    return wrapper


async def serve() -> None:
    from agentfx.api import main
    from agentfx.api.workers import candle_ingestion, evolution_runner, paper_runner

    async def ingestion_loop():
        record("ingestion", pid=os.getpid())
        await asyncio.Event().wait()

    paper_runner._subprocess_entry = paper_worker
    evolution_runner._subprocess_entry = evolution_worker
    candle_ingestion.ingestion_loop = ingestion_loop
    main.recover_orphaned_paper_runs = _recording(main.recover_orphaned_paper_runs)
    main.recover_queued_evolution_runs = _recording(main.recover_queued_evolution_runs)

    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    async with main.lifespan(main.app):
        record("started", pid=os.getpid())
        await stop.wait()


if __name__ == "__main__":
    asyncio.run(serve())
