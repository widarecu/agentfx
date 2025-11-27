from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentfx.shared.config import IngestConfig, load_config
from agentfx.adapters.oanda import OandaClient
from agentfx.adapters.storage.sqlite import Registry
from agentfx.adapters.storage.csv import write_candles_csv, latest_timestamp
from dateutil import parser as dtparser


def _calc_start_end(now_utc: datetime, start_days: int) -> tuple[datetime, datetime]:
    end = now_utc.replace(second=0, microsecond=0)
    start = end - timedelta(days=start_days)
    return start, end


def backfill_pair(
    cfg: IngestConfig, client: OandaClient, reg: Registry, instrument: str
) -> None:
    run_id = reg.start_run(instrument, cfg.granularity)
    rows = 0
    gaps_fixed = 0
    try:
        now = datetime.now(timezone.utc)
        existing_end = (
            None
            if cfg.force_start
            else latest_timestamp(cfg.data_root, instrument, cfg.granularity)
        )
        if existing_end is None:
            start, end = _calc_start_end(now, cfg.start_days)
        else:
            start = existing_end + timedelta(minutes=1)
            end = now
        cur = start
        first_written: datetime | None = None
        last_written: datetime | None = None
        while cur < end:
            candles = client.get_candles(
                instrument=instrument,
                granularity=cfg.granularity,
                price="M",
                from_ts=cur,
                to_ts=None,
                count=cfg.chunk_size,
                include_first=True,
            )
            if not candles:
                break
            written, chunk_min, chunk_max = write_candles_csv(
                cfg.data_root, instrument, cfg.granularity, candles
            )
            rows += written
            if first_written is None and chunk_min is not None:
                first_written = chunk_min
            if chunk_max is not None:
                last_written = chunk_max
            last_ts = candles[-1]["time"]
            last_dt = dtparser.isoparse(last_ts)
            cur = last_dt + timedelta(minutes=1)
            if cur <= start:
                break
        if rows > 0 and first_written is not None and last_written is not None:
            reg.upsert_dataset(
                instrument,
                cfg.granularity,
                start_ts=first_written.replace(tzinfo=timezone.utc).isoformat(),
                end_ts=last_written.replace(tzinfo=timezone.utc).isoformat(),
            )
        reg.finish_run(
            run_id, rows_ingested=rows, gaps_fixed=gaps_fixed, status="success"
        )
    except Exception:
        reg.finish_run(
            run_id, rows_ingested=rows, gaps_fixed=gaps_fixed, status="error"
        )
        raise


def backfill_all(cfg: IngestConfig | None = None) -> None:
    cfg = cfg or load_config()
    reg = Registry(db_path=cfg.db_path)
    reg.init()
    client = OandaClient()
    for pair in cfg.pairs:
        backfill_pair(cfg, client, reg, pair)
