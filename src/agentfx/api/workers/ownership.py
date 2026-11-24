from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

_ACQUIRE = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_RELEASE = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")
_TRANSACTION_LOCK = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")


@contextmanager
def worker_lock(engine: Engine, key: str) -> Iterator[tuple[bool, Connection | None]]:
    if engine.dialect.name == "sqlite":
        yield True, None
        return

    with engine.connect() as connection:
        acquired = bool(connection.scalar(_ACQUIRE, {"key": key}))
        owner_session = connection.connection.dbapi_connection
        ownership_lost = False

        def require_owner_session(conn, *args):
            nonlocal ownership_lost
            if conn.connection.dbapi_connection is not owner_session:
                ownership_lost = True
                raise RuntimeError(f"Database session lost worker lock: {key}")

        if acquired:
            event.listen(connection, "before_cursor_execute", require_owner_session)
        try:
            connection.commit()
            yield acquired, connection
        finally:
            if acquired and not ownership_lost and not connection.invalidated:
                try:
                    connection.rollback()
                    connection.execute(_RELEASE, {"key": key})
                    connection.commit()
                except BaseException:
                    connection.invalidate()
                    raise


def try_recovery_lock(db: Session, key: str) -> bool:
    if db.get_bind().dialect.name == "sqlite":
        return True
    return bool(db.scalar(_TRANSACTION_LOCK, {"key": key}))
