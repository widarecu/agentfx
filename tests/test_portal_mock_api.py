import json
from datetime import datetime

from fastapi.testclient import TestClient

from agentfx.portal_mock.app import app, RUN_ID, PAIR, STRATEGY_ID


client = TestClient(app)


def test_candles_basic():
    r = client.get(f"/api/pairs/{PAIR}/candles", params={"tf": "M5", "limit": 10})
    assert r.status_code == 200
    data = r.json()
    assert data["tf"] == "M5"
    assert isinstance(data["items"], list)
    assert len(data["items"]) <= 10
    if data["items"]:
        c0 = data["items"][0]
        for k in ["ts", "o", "h", "l", "c", "v"]:
            assert k in c0


def test_runs_listing_and_detail():
    # List runs
    r = client.get("/api/runs", params={"env": "paper", "pair": PAIR, "strategy": STRATEGY_ID})
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["run_id"] == RUN_ID for it in items)

    # Get run detail
    r2 = client.get(f"/api/runs/{RUN_ID}")
    assert r2.status_code == 200
    run = r2.json()
    assert run["pair"] == PAIR
    assert run["strategy_id"] == STRATEGY_ID
    assert "metrics" in run and "params" in run


def test_run_scoped_endpoints():
    # Trades
    r = client.get(f"/api/runs/{RUN_ID}/trades", params={"limit": 5})
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    if data["items"]:
        t0 = data["items"][0]
        for k in [
            "trade_id",
            "side",
            "qty",
            "entry_ts",
            "entry_price",
            "exit_ts",
            "exit_price",
            "gross_pnl",
            "net_pnl",
            "return_pct",
            "duration_s",
            "mae",
            "mfe",
        ]:
            assert k in t0

    # Orders
    r = client.get(f"/api/runs/{RUN_ID}/orders", params={"limit": 5})
    assert r.status_code == 200
    data = r.json()
    assert "items" in data

    # Signals
    r = client.get(
        f"/api/runs/{RUN_ID}/signals",
        params={"series": "ma_fast,ma_slow,rsi", "limit": 1000},
    )
    assert r.status_code == 200
    sig = r.json()
    assert "series" in sig
    names = {s["name"] for s in sig["series"]}
    assert {"ma_fast", "ma_slow", "rsi"}.issubset(names)

    # Position bands
    r = client.get(f"/api/runs/{RUN_ID}/position_bands")
    assert r.status_code == 200
    pb = r.json()
    assert "items" in pb


def test_strategies_catalog_and_latest_run():
    # Catalog
    r = client.get("/api/strategies", params={"env": "paper", "status": "enabled"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert isinstance(items, list)

    # Strategy detail
    if items:
        sid = items[0]["id"]
        r2 = client.get(f"/api/strategies/{sid}")
        assert r2.status_code == 200
        s = r2.json()
        assert s["id"] == sid

    # Latest run convenience
    r3 = client.get(f"/api/strategies/{STRATEGY_ID}/latest-run", params={"env": "paper", "pair": PAIR})
    assert r3.status_code == 200
    latest = r3.json()
    assert latest["run_id"] == RUN_ID
