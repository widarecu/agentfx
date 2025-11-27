import pytest

from agentfx.portal_mock.app import SIGNALS, RUNS, ORDERS, TRADES


@pytest.mark.unit
def test_signals_series_names_and_types():
    names = {s["name"] for s in SIGNALS["series"]}
    assert {"ma_fast", "ma_slow", "rsi", "donchian", "decisions"}.issubset(names)
    type_map = {s["name"]: s["type"] for s in SIGNALS["series"]}
    assert type_map["ma_fast"] == "line"
    assert type_map["rsi"] == "oscillator"


@pytest.mark.unit
def test_runs_metrics_presence():
    assert RUNS
    m = RUNS[0]["metrics"]
    for k in [
        "pnl_daily",
        "pnl_cum",
        "currency",
        "sharpe",
        "mdd",
        "hit_rate",
        "turnover",
        "cagr",
    ]:
        assert k in m


@pytest.mark.unit
def test_trade_has_mae_mfe_and_pnl():
    assert TRADES
    t = TRADES[0]
    for k in ["gross_pnl", "net_pnl", "mae", "mfe"]:
        assert k in t
    assert isinstance(t["mae"], dict) and isinstance(t["mfe"], dict)


@pytest.mark.unit
def test_orders_have_exec_and_provenance():
    assert ORDERS
    o = ORDERS[0]
    assert "exec" in o and "provenance_id" in o
