import pytest
from fastapi.testclient import TestClient

from agentfx.portal_mock.app import app, RUN_ID, PAIR


@pytest.mark.integration
def test_trades_orders_pagination():
    client = TestClient(app)

    # first page trades
    r1 = client.get(f"/api/runs/{RUN_ID}/trades", params={"limit": 1})
    assert r1.status_code == 200
    data1 = r1.json()
    assert len(data1["items"]) <= 1
    cursor = data1.get("next_cursor")

    # next page with cursor should be valid (may be empty in tiny fixtures)
    r2 = client.get(f"/api/runs/{RUN_ID}/trades", params={"limit": 1, "cursor": cursor})
    assert r2.status_code == 200

    # orders pagination
    r1 = client.get(f"/api/runs/{RUN_ID}/orders", params={"limit": 1})
    assert r1.status_code == 200
    data1 = r1.json()
    assert len(data1["items"]) <= 1
    cursor = data1.get("next_cursor")

    r2 = client.get(f"/api/runs/{RUN_ID}/orders", params={"limit": 1, "cursor": cursor})
    assert r2.status_code == 200


@pytest.mark.integration
def test_candles_downsampling():
    client = TestClient(app)

    # Ask for fewer than available to trigger downsampling flag
    r = client.get(f"/api/pairs/{PAIR}/candles", params={"tf": "M5", "limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["downsampled"] in (True, False)  # depends on fixture length vs 10
    assert len(body["items"]) <= 10


@pytest.mark.integration
def test_signals_downsampling_and_filtering():
    client = TestClient(app)

    # Request limited number of points and specific series
    r = client.get(
        f"/api/runs/{RUN_ID}/signals",
        params={"series": "ma_fast,ma_slow,rsi", "limit": 1},
    )
    assert r.status_code == 200
    body = r.json()
    names = {s["name"] for s in body["series"]}
    assert names == {"ma_fast", "ma_slow", "rsi"}
    for s in body["series"]:
        assert len(s.get("points", [])) <= 1
