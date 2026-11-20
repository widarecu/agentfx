"""AFX-50: one signal fire places at most one order, across processes.

Unit tests cover the runner's control flow with a fake `orders` table.
These run the two claims against real Postgres so the guarantee rests
on the database's own uniqueness and row-update semantics, with each
"runner" on its own connection as separate processes would be.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration


@pytest.fixture
def committed_run(test_engine):
    from agentfx.api.core.security import get_password_hash
    from agentfx.api.models import Order, Run, RunEnvironment, RunStatus, User

    Session = sessionmaker(bind=test_engine)
    db = Session()
    unique = uuid4().hex[:8]
    user = User(
        email=f"afx50_{unique}@example.com",
        hashed_password=get_password_hash("stub"),
        role="trader",
        is_active=True,
    )
    db.add(user)
    db.commit()
    run = Run(
        id=f"run_{int(time.time() * 1000)}_paper_afx50_{unique}",
        pair="EUR_USD",
        environment=RunEnvironment.PAPER,
        status=RunStatus.APPROVED,
        parameters={},
        user_id=user.id,
    )
    db.add(run)
    db.commit()
    run_id, user_id = run.id, user.id
    db.close()

    yield Session, run_id

    db = Session()
    db.query(Order).filter(Order.run_id == run_id).delete()
    db.query(Run).filter(Run.id == run_id).delete()
    db.query(User).filter(User.id == user_id).delete()
    db.commit()
    db.close()


def _race(n: int, fn):
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def _worker(i: int) -> None:
        barrier.wait()
        results[i] = fn()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_order_claims_for_same_bar_have_one_winner(committed_run):
    from agentfx.api.models import Order, Run
    from agentfx.services.paper_runner import _claim_order_slot

    Session, run_id = committed_run
    bar = datetime(2026, 10, 22, 9, 31, tzinfo=UTC)

    def _claim():
        db = Session()
        try:
            run = db.get(Run, run_id)
            row = _claim_order_slot(db, run, signal_bar_ts=bar, side="buy", units=1000)
            return row is not None
        finally:
            db.close()

    assert sorted(_race(4, _claim)) == [False, False, False, True]

    db = Session()
    assert db.query(Order).filter(Order.run_id == run_id).count() == 1
    other_side = _claim_order_slot(
        db, db.get(Run, run_id), signal_bar_ts=bar, side="sell", units=1000
    )
    assert other_side is not None
    db.close()


def test_concurrent_runner_claims_have_one_winner(committed_run):
    from agentfx.api.models import Run, RunStatus
    from agentfx.services.paper_runner import _claim_runner

    Session, run_id = committed_run

    def _claim():
        db = Session()
        try:
            return _claim_runner(db, db.get(Run, run_id))
        finally:
            db.close()

    assert sorted(_race(3, _claim)) == [False, False, True]

    db = Session()
    assert db.get(Run, run_id).status == RunStatus.RUNNING
    db.close()
