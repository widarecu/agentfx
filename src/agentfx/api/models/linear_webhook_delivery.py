"""SQLAlchemy model for Linear Agent webhook deliveries (AFX-66 Phase 1).

One row per Linear Agent webhook POST we've received. The primary
reason this table exists is **idempotency**: Linear retries webhook
deliveries on non-2xx or timeout, and the `Linear-Delivery` header
is documented as a per-delivery UUID. Making it the primary key
means the second attempt at the same delivery hits an IntegrityError
on insert and the handler can return 200 without reprocessing.

`raw_body` is deliberately persisted during Phase 1 so we can learn
the exact payload shape Linear sends in practice — the developer
docs don't name every field explicitly (e.g. the webhook field that
carries the agent session id), and empirical confirmation is
required before Phase 2 wires the dispatch path.
"""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from ..core.database import Base


class LinearWebhookDelivery(Base):
    """One row per webhook delivery from Linear's Agent platform.

    - `delivery_id`: the `Linear-Delivery` header value (UUID per
      delivery per the webhook docs). PK so duplicates raise
      IntegrityError on insert — the handler maps that to a 200
      duplicate response.
    - `received_at`: when we accepted the request. Separate from
      `webhookTimestamp` in the body (which is Linear's send time).
    - `event_action`: `"created" | "prompted" | …` from the body.
      Indexed for Phase 2 queries that count dispatch-trigger events.
    - `agent_session_id`: best-effort extraction of the session id
      from the body. Nullable because the exact field name is not
      yet confirmed; indexed so lookups by session id are cheap
      once it is.
    - `raw_body`: the parsed JSON body. Phase 1 observability — can
      be nulled or trimmed once Phase 2 dispatch rules settle.
    - `processing_status`: `"logged" | "dispatched" | "duplicate" | …`
      so the row records what the handler did, not just that an
      event arrived.
    """

    __tablename__ = "linear_webhook_deliveries"

    delivery_id = Column(String(64), primary_key=True)
    received_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    event_action = Column(String(32), nullable=True, index=True)
    agent_session_id = Column(String(64), nullable=True, index=True)
    # JSON on SQLite (unit tests), JSONB on Postgres (prod/integration).
    # Keeps the test suite portable without losing Postgres's indexable
    # binary storage in production.
    raw_body = Column(
        JSON().with_variant(JSONB(astext_type=Text()), "postgresql"),
        nullable=True,
    )
    processing_status = Column(
        String(32),
        nullable=False,
        server_default="logged",
    )
