import pytest
from fastapi.testclient import TestClient

from agentfx.portal_mock.app import PAIR, RUN_ID, STRATEGY_ID, app


@pytest.mark.integration
def test_candles_endpoint():
    client = TestClient(app)
    r = client.get(f"/api/pairs/{PAIR}/candles", params={"tf": "M5", "limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["tf"] == "M5"
    assert isinstance(body["items"], list)


@pytest.mark.integration
def test_runs_and_detail_endpoints():
    client = TestClient(app)
    r = client.get(
        "/api/runs", params={"env": "paper", "pair": PAIR, "strategy": STRATEGY_ID}
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["run_id"] == RUN_ID for it in items)

    r2 = client.get(f"/api/runs/{RUN_ID}")
    assert r2.status_code == 200
    run = r2.json()
    assert run["pair"] == PAIR


@pytest.mark.integration
def test_run_scoped_trades_orders_signals_position_bands():
    client = TestClient(app)

    r = client.get(f"/api/runs/{RUN_ID}/trades", params={"limit": 5})
    assert r.status_code == 200

    r = client.get(f"/api/runs/{RUN_ID}/orders", params={"limit": 5})
    assert r.status_code == 200

    r = client.get(
        f"/api/runs/{RUN_ID}/signals", params={"series": "ma_fast,ma_slow,rsi"}
    )
    assert r.status_code == 200

    r = client.get(f"/api/runs/{RUN_ID}/position_bands")
    assert r.status_code == 200


@pytest.mark.integration
def test_strategies_catalog_and_latest_run():
    client = TestClient(app)

    r = client.get("/api/strategies", params={"env": "paper", "status": "enabled"})
    assert r.status_code == 200

    r = client.get(f"/api/strategies/{STRATEGY_ID}")
    assert r.status_code == 200

    r = client.get(
        f"/api/strategies/{STRATEGY_ID}/latest-run",
        params={"env": "paper", "pair": PAIR},
    )
    assert r.status_code == 200
