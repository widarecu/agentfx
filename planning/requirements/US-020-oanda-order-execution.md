# US-020: OANDA Order-Execution Adapter Methods

**Version:** v0.6.0 — Paper Trading MVP
**Priority:** P0 — Must Have (blocks paper runner)
**Status:** TODO

## Given-When-Then

**GIVEN** the paper runner has decrypted the owning user's OANDA credentials and constructed a scoped `OandaClient`
  **AND** the runner has decided a signal-vs-position diff requires a market order
**WHEN** the runner calls `client.place_market_order(instrument, units, stop_loss_pips, take_profit_pips)`
**THEN** the adapter:

1. Calls OANDA v20 `POST /v3/accounts/{account_id}/orders` with a `MARKET` order specification
2. Returns `{order_id, trade_id, fill_price}` on success
3. Retries with exponential backoff on transient errors (500 / 502 / 503 / 504 / timeout)
4. Increments the per-client circuit-breaker counter on non-transient failures (400 / 401 / 403); when 5 consecutive failures occur within 60 seconds, opens the breaker and raises `OandaCircuitOpen`

  **AND** the paper runner catches `OandaCircuitOpen` and halts the run with `stop_reason=BROKER_CIRCUIT`, close-all-positions if possible.

## Acceptance Criteria

### Adapter methods added to `src/agentfx/adapters/oanda.py`

All accept an implicit `OandaClient` instance carrying user-specific credentials.

- [ ] `place_market_order(instrument: str, units: int, stop_loss_pips: float | None = None, take_profit_pips: float | None = None) -> {order_id: str, trade_id: str, fill_price: float, fill_time: datetime}`
  - Positive `units` = long, negative = short
  - `stop_loss_pips` translates to an OANDA stop-loss order at fill_price ± (pips × pip_size)
  - `take_profit_pips` translates similarly
- [ ] `close_trade(trade_id: str, units: str = "ALL") -> {realized_pnl: float, realized_at: datetime}`
- [ ] `close_all_positions(instrument: str) -> list[{trade_id, realized_pnl}]`
- [ ] `get_position(instrument: str) -> {long_units: int, short_units: int, unrealized_pnl: float, avg_price: float | None}`
- [ ] `get_account_balance() -> {balance: float, nav: float, margin_used: float, margin_available: float, currency: str}`
- [ ] `get_open_trades() -> list[{trade_id, instrument, units, avg_price, unrealized_pnl, opened_at, stop_loss_price, take_profit_price}]`
- [ ] `get_pricing(instruments: list[str]) -> dict[instrument, {bid: float, ask: float, spread_pips: float, timestamp: datetime}]`

### Retry + circuit breaker

- [ ] Exponential backoff: base 500ms, factor 2, max 5 retries, jitter ±20%, only for 500 / 502 / 503 / 504 / connection timeout
- [ ] Non-retryable errors (400 / 401 / 403 / 404) raise immediately with sanitised error message
- [ ] Per-`OandaClient` circuit breaker: counts consecutive failures over a 60-second window; on 5 failures opens the breaker and raises `OandaCircuitOpen`. Successful calls reset the count.
- [ ] Circuit is per-client-instance, not global — a failure on User A's credentials does not affect User B's paper runner

### Tests

- [ ] `tests/unit/adapters/test_oanda_orders.py` using `pytest-httpx`:
  - `place_market_order` success on 200 with mocked OANDA response
  - `place_market_order` retries on 503 (assert 3 retries then success)
  - `place_market_order` fails fast on 401
  - `close_trade`, `close_all_positions`, `get_position`, `get_account_balance`, `get_open_trades`, `get_pricing` — success + failure paths
  - Circuit breaker: 5 consecutive 503s → `OandaCircuitOpen` on the 6th call; successful call resets counter
- [ ] Integration test (respx-based fake OANDA server) in US-021 exercises the end-to-end order flow

## API Contract (adapter-level, not HTTP)

```python
client = OandaClient(
    api_key=user_creds.decrypted_api_key,
    account_id=user_creds.oanda_account_id,
    environment="PRACTICE",
)

result = client.place_market_order(
    instrument="EUR_USD",
    units=10000,  # long 10k
    stop_loss_pips=20,
    take_profit_pips=40,
)
# result -> {"order_id": "1234", "trade_id": "5678", "fill_price": 1.10234, "fill_time": datetime(...)}

position = client.get_position("EUR_USD")
# position -> {"long_units": 10000, "short_units": 0, "unrealized_pnl": 12.34, "avg_price": 1.10234}

closed = client.close_trade(trade_id="5678")
# closed -> {"realized_pnl": 45.67, "realized_at": datetime(...)}
```

## Files Involved

- `src/agentfx/adapters/oanda.py` (edit — extend `OandaClient`)
- `src/agentfx/adapters/oanda_errors.py` (new — `OandaCircuitOpen` exception + retry/backoff decorator)
- `tests/unit/adapters/test_oanda_orders.py` (new)

## Verification

1. `make test-unit` — new adapter suite passes with mocked OANDA responses
2. Manual: point a scratch `OandaClient` at Wilson's OANDA Practice account, call `get_account_balance()` and `place_market_order(EUR_USD, 100, 20, 40)`; verify OANDA dashboard shows the trade
3. Manual circuit breaker: use `pytest-httpx` inline to simulate 5 consecutive 503s in the scratch test, assert `OandaCircuitOpen` raised
