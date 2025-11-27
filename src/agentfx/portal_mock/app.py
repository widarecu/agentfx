from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Response, Request
from pydantic import BaseModel
from agentfx.portal_mock.utils import paginate_list, downsample_time_series

app = FastAPI(title="AgentFX Portal Mock API", version="0.1")

# --- In-memory fixtures ---
NOW = datetime(2025, 11, 18, 1, 2, 3, tzinfo=timezone.utc)
PAIR = "AUDUSD"
RUN_ID = "run_20251117_ma_AUDUSD_paper_01"
STRATEGY_ID = "ma"

class Candle(BaseModel):
    ts: datetime
    o: float
    h: float
    l: float
    c: float
    v: int

CANDLES: List[Candle] = []
start = NOW - timedelta(days=1)
for i in range(60 * 24 // 5):  # M5 candles for ~1 day
    ts = start + timedelta(minutes=5 * i)
    base = 0.655 + 0.0001 * (i % 10)
    CANDLES.append(
        Candle(ts=ts, o=base, h=base + 0.0002, l=base - 0.0002, c=base + 0.00005, v=1000 + i)
    )

RUNS: List[Dict[str, Any]] = [
    {
        "run_id": RUN_ID,
        "pair": PAIR,
        "strategy_id": STRATEGY_ID,
        "env": "paper",
        "status": "enabled",
        "last_updated": NOW.isoformat().replace("+00:00", "Z"),
        "params": {"strategy_params": {"fast": 20, "slow": 50}},
        "metrics": {
            "pnl_daily": -12.34,
            "pnl_cum": -532.1,
            "currency": "USD",
            "sharpe": -0.42,
            "mdd": -0.31,
            "hit_rate": 0.47,
            "turnover": 320,
            "cagr": -0.75,
        },
    }
]

TRADES = [
    {
        "trade_id": "t_20251117_001",
        "side": "long",
        "qty": 1.0,
        "entry_ts": (NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
        "entry_price": 0.65400,
        "exit_ts": (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        "exit_price": 0.65440,
        "gross_pnl": 40.0,
        "net_pnl": 38.5,
        "currency": "USD",
        "return_pct": 0.0612,
        "duration_s": 3600,
        "mae": {"pips": -6.0, "pct": -0.0092, "atr_mult": 0.4},
        "mfe": {"pips": 12.0, "pct": 0.0184, "atr_mult": 0.8},
        "provenance_id": "dec_abc123",
    }
]

ORDERS = [
    {
        "order_id": "o_20251117_1001",
        "ts": (NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
        "type": "market",
        "side": "long",
        "qty": 1.0,
        "price": 0.65402,
        "status": "filled",
        "exec": {"fill_qty": 1.0, "fill_price": 0.65402, "latency_ms": 120},
        "provenance_id": "dec_abc123",
        "synthetic": True,
    }
]

# Build simple moving averages from mock candles to power overlays
def _sma(values: List[float], window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    acc = 0.0
    buf: List[float] = []
    for i, v in enumerate(values):
        buf.append(v)
        acc += v
        if len(buf) > window:
            acc -= buf[-window-1]
        if len(buf) >= window:
            out.append(acc / window)
        else:
            out.append(None)
    return out

close_vals = [c.c for c in CANDLES]
fast_ma = _sma(close_vals, 10)
slow_ma = _sma(close_vals, 20)

ma_fast_points = [
    {"ts": c.ts.isoformat().replace("+00:00", "Z"), "value": v}
    for c, v in zip(CANDLES, fast_ma)
    if v is not None
]
ma_slow_points = [
    {"ts": c.ts.isoformat().replace("+00:00", "Z"), "value": v}
    for c, v in zip(CANDLES, slow_ma)
    if v is not None
]

SIGNALS = {
    "series": [
        {"name": "ma_fast", "type": "line", "unit": "price", "points": ma_fast_points},
        {"name": "ma_slow", "type": "line", "unit": "price", "points": ma_slow_points},
        {
            "name": "rsi",
            "type": "oscillator",
            "unit": "index",
            "meta": {"min": 0, "max": 100, "bands": [30, 70]},
            "points": [],
        },
        {
            "name": "donchian",
            "type": "channel",
            "unit": "price",
            "points": [],
        },
        {
            "name": "decisions",
            "type": "marker",
            "unit": "index",
            "points": [],
        },
    ],
    "downsampled": False,
}

STRATEGIES = [
    {
        "id": STRATEGY_ID,
        "name": "MA Crossover",
        "env": "paper",
        "status": "enabled",
        "supported_overlays": ["ma_fast", "ma_slow"],
        "defaults": {"fast": 20, "slow": 50},
    }
]

# --- Helpers ---
def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")

# --- Auth (mock) ---
SESSION_COOKIE = "session"
_SESSIONS: dict[str, dict] = {}

class LoginPayload(BaseModel):
    email: str
    password: str
    remember: bool = False


@app.post("/api/auth/login")
def login(payload: LoginPayload, response: Response):
    if not payload.email or not payload.password:
        raise HTTPException(status_code=400, detail="email and password required")
    # Simple mock rule: only password 'secret' succeeds
    if payload.password != "secret":
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = f"mock-{payload.email}"
    user = {"email": payload.email, "role": "viewer"}
    _SESSIONS[token] = user
    max_age = 60 * 60 * 24 * 7 if payload.remember else None
    response.set_cookie(key=SESSION_COOKIE, value=token, httponly=True, samesite="lax", max_age=max_age)
    return {"user": user}


@app.get("/api/auth/me")
def me(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or token not in _SESSIONS:
        raise HTTPException(status_code=401, detail="unauthenticated")
    return {"user": _SESSIONS[token]}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in _SESSIONS:
        _SESSIONS.pop(token, None)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}

# --- Routes ---
@app.get("/api/pairs/{pair}/candles")
def get_candles(pair: str, tf: str = Query("M5"), from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, limit: int = 5000):
    if pair != PAIR:
        return {"tf": tf, "from": from_, "to": to, "items": [], "downsampled": False, "next_cursor": None}
    # Downsample if needed based on limit
    points = [c.model_dump() for c in CANDLES]
    if limit and limit > 0:
        points, down = downsample_time_series(points, limit)
    else:
        down = False
    items = points
    return {
        "tf": tf,
        "from": from_ or iso(CANDLES[0].ts),
        "to": to or iso(CANDLES[-1].ts),
        "items": items,
        "downsampled": down,
        "next_cursor": None,
    }

@app.get("/api/runs")
def list_runs(env: Optional[str] = None, pair: Optional[str] = None, strategy: Optional[str] = None, status: Optional[str] = None, limit: int = 100, cursor: Optional[str] = None):
    items = RUNS
    if env:
        items = [r for r in items if r["env"] == env]
    if pair:
        items = [r for r in items if r["pair"] == pair]
    if strategy:
        items = [r for r in items if r["strategy_id"] == strategy]
    if status:
        items = [r for r in items if r["status"] == status]
    return {"items": items[:limit], "next_cursor": None}

@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    for r in RUNS:
        if r["run_id"] == run_id:
            return r
    raise HTTPException(status_code=404, detail="run not found")

@app.get("/api/runs/{run_id}/trades")
def get_trades(run_id: str, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, limit: int = 1000, cursor: Optional[str] = None):
    if run_id != RUN_ID:
        return {"items": [], "next_cursor": None}
    items, next_cursor = paginate_list(TRADES, cursor, limit)
    return {"items": items, "next_cursor": next_cursor}

@app.get("/api/runs/{run_id}/orders")
def get_orders(run_id: str, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, limit: int = 1000, cursor: Optional[str] = None):
    if run_id != RUN_ID:
        return {"items": [], "next_cursor": None}
    items, next_cursor = paginate_list(ORDERS, cursor, limit)
    return {"items": items, "next_cursor": next_cursor}

@app.get("/api/runs/{run_id}/signals")
def get_signals(run_id: str, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, limit: int = 5000, series: Optional[str] = None):
    if run_id != RUN_ID:
        return {"series": [], "downsampled": False}
    if series:
        series_names = set([s.strip() for s in series.split(",") if s.strip()])
        selected = [s for s in SIGNALS["series"] if s["name"] in series_names]
    else:
        selected = SIGNALS["series"]
    downsampled_any = False
    limited_series: List[Dict[str, Any]] = []
    for s in selected:
        pts = s.get("points", [])
        pts_limited, down = downsample_time_series(pts, limit if limit else len(pts))
        downsampled_any = downsampled_any or down
        s_copy = {k: v for k, v in s.items() if k != "points"}
        s_copy["points"] = pts_limited
        limited_series.append(s_copy)
    return {"series": limited_series, "downsampled": downsampled_any}

@app.get("/api/runs/{run_id}/position_bands")
def get_position_bands(run_id: str, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
    if run_id != RUN_ID:
        return {"items": []}
    return {
        "items": [
            {"start": iso(NOW - timedelta(hours=3)), "end": iso(NOW - timedelta(hours=2)), "side": "long"},
            {"start": iso(NOW - timedelta(hours=1)), "end": iso(NOW), "side": "short"},
        ]
    }

@app.get("/api/strategies")
def list_strategies(env: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None, limit: int = 50, cursor: Optional[str] = None):
    items = STRATEGIES
    if q:
        items = [s for s in items if q.lower() in s["name"].lower()]
    return {"items": items[:limit], "next_cursor": None}

@app.get("/api/strategies/{strategy_id}")
def get_strategy(strategy_id: str):
    for s in STRATEGIES:
        if s["id"] == strategy_id:
            return s
    raise HTTPException(status_code=404, detail="strategy not found")

@app.get("/api/strategies/{strategy_id}/latest-run")
def get_latest_run(strategy_id: str, env: Optional[str] = None, pair: Optional[str] = None):
    for r in RUNS:
        if r["strategy_id"] == strategy_id and (env is None or r["env"] == env) and (pair is None or r["pair"] == pair):
            return {
                "run_id": r["run_id"],
                "pair": r["pair"],
                "env": r["env"],
                "strategy_id": r["strategy_id"],
                "last_updated": r["last_updated"],
            }
    raise HTTPException(status_code=404, detail="latest run not found")
