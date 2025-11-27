from __future__ import annotations

import os
from datetime import datetime
from typing import List, Dict

import pandas as pd


def _csv_path(root: str, instrument: str, granularity: str, ts: datetime) -> str:
    year = ts.strftime("%Y")
    month = ts.strftime("%m")
    dir_path = os.path.join(root, instrument, granularity, f"{year}-{month}")
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{year}-{month}.csv")


def write_candles_csv(root: str, instrument: str, granularity: str, candles: List[Dict]) -> tuple[int, datetime, datetime]:
    if not candles:
        return 0, None, None  # type: ignore
    # Normalize to DataFrame
    df = pd.DataFrame(candles)
    # Ensure UTC time and ordering
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time")

    overall_min = df["time"].min().to_pydatetime()
    overall_max = df["time"].max().to_pydatetime()

    # Partition by month using explicit strftime to avoid tz warning
    written = 0
    for month_key, group in df.groupby(df["time"].dt.strftime("%Y-%m")):
        ts_for_path = group["time"].iloc[0].to_pydatetime()
        path = _csv_path(root, instrument, granularity, ts_for_path)
        if os.path.exists(path):
            existing = pd.read_csv(path)
            if not existing.empty:
                existing["time"] = pd.to_datetime(existing["time"], utc=True)
            merged = pd.concat([existing, group], ignore_index=True)
            merged = merged.drop_duplicates(subset=["time"]).sort_values("time")
            merged.to_csv(path, index=False)
            written += len(group)
        else:
            group.to_csv(path, index=False)
            written += len(group)
    return written, overall_min, overall_max


def latest_timestamp(root: str, instrument: str, granularity: str) -> datetime | None:
    base = os.path.join(root, instrument, granularity)
    if not os.path.isdir(base):
        return None
    months = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    if not months:
        return None
    months.sort(reverse=True)
    for m in months:
        csv_path = os.path.join(base, m, f"{m}.csv")
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            if df.empty:
                continue
            df["time"] = pd.to_datetime(df["time"], utc=True)
            return df["time"].max().to_pydatetime()
    return None
