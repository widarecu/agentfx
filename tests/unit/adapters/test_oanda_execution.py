"""Unit tests for the v0.6.0 OANDA order execution surface (US-020).

Split into its own file to keep `test_oanda.py` focused on the
pre-existing v0.3.0 candle / financing surface. These tests exercise:

- `OandaCircuitBreaker` state transitions
- `_backoff_delay_seconds` bounds + jitter shape
- `_pips_to_price_distance` for JPY vs non-JPY quotes
- The order/position/pricing wrappers via `session.request` mocks
- `OandaCircuitOpen` semantics: raised on the NEXT call after the
  threshold is reached, releases after the window elapses

Uses `unittest.mock` (matches the existing pattern in
`test_oanda.py`). No `httpx` / `respx` — the client is `requests`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.agentfx.adapters.oanda import (
    OandaCircuitBreaker,
    OandaCircuitOpen,
    OandaClient,
    OandaError,
    _backoff_delay_seconds,
    _pips_to_price_distance,
)

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_session():
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    return session


@pytest.fixture
def client(mock_session):
    """OandaClient wired with a mock session and an explicit account id."""
    with (
        patch(
            "src.agentfx.adapters.oanda.oanda_api_key",
            return_value="test-token",
        ),
        patch(
            "src.agentfx.adapters.oanda.oanda_api_host",
            return_value="api-fxpractice.oanda.com",
        ),
        patch("requests.Session", return_value=mock_session),
    ):
        c = OandaClient(account_id="101-004-12345678-001")
    return c


def _http_ok(json_body: dict):
    """Build a MagicMock response with status 200 + JSON body."""
    r = MagicMock()
    r.status_code = 200
    r.content = b"non-empty"
    r.json.return_value = json_body
    return r


def _http_error(status_code: int, text: str = ""):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    r.content = text.encode() if text else b""
    return r


# ─────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────


class TestBackoffDelay:
    def test_grows_exponentially(self):
        # Compare medians across many samples so jitter can't dominate.
        prev_lo = 0.0
        for attempt in range(4):
            samples = [
                _backoff_delay_seconds(attempt, base_ms=500, cap_ms=8000)
                for _ in range(50)
            ]
            lo = min(samples)
            # Base doubles each step; the min is `base_ms * 2**attempt`
            # (jitter is non-negative), so successive minimums must be
            # strictly increasing until the cap.
            assert lo >= prev_lo
            prev_lo = lo

    def test_capped(self):
        # Very high attempt count must not blow past the cap + 25% jitter.
        for _ in range(20):
            d = _backoff_delay_seconds(20, base_ms=500, cap_ms=8000)
            assert d <= 8.0 * 1.25 + 1e-6

    def test_always_positive(self):
        for attempt in range(0, 10):
            d = _backoff_delay_seconds(attempt)
            assert d > 0


class TestPipsToDistance:
    def test_non_jpy_uses_10000(self):
        # 10 pips on EUR_USD = 0.0010
        assert _pips_to_price_distance("EUR_USD", 10) == "0.00100"

    def test_jpy_uses_100(self):
        # 10 pips on USD_JPY = 0.10
        assert _pips_to_price_distance("USD_JPY", 10) == "0.10000"


# ─────────────────────────────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = OandaCircuitBreaker(threshold=5, window_seconds=60)
        assert cb.should_reject() is False

    def test_trips_after_threshold_failures(self):
        cb = OandaCircuitBreaker(threshold=3, window_seconds=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.should_reject() is False
        cb.record_failure()
        assert cb.should_reject() is True

    def test_success_resets(self):
        cb = OandaCircuitBreaker(threshold=3, window_seconds=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert cb.should_reject() is False

    def test_stale_failures_age_out(self):
        cb = OandaCircuitBreaker(threshold=3, window_seconds=60)
        # Simulate 3 failures at t=0. Then advance the clock so all
        # are outside the window on the next check.
        with patch("time.monotonic") as clock:
            clock.return_value = 0.0
            cb.record_failure()
            cb.record_failure()
            cb.record_failure()
            assert cb.should_reject() is True
            clock.return_value = 61.0
            assert cb.should_reject() is False


# ─────────────────────────────────────────────────────────────────────
# _request behaviour
# ─────────────────────────────────────────────────────────────────────


class TestRequestBehaviour:
    def test_5xx_retries_then_succeeds(self, client, mock_session):
        mock_session.request.side_effect = [
            _http_error(500, "internal"),
            _http_error(503, "unavail"),
            _http_ok({"ok": True}),
        ]
        with patch("time.sleep"):
            out = client._request("GET", "/test")
        assert out == {"ok": True}
        assert mock_session.request.call_count == 3

    def test_4xx_fails_immediately_no_retry(self, client, mock_session):
        # 401 must NOT retry — the token isn't going to change.
        mock_session.request.side_effect = [_http_error(401, "unauthorized")]
        with patch("time.sleep"):
            with pytest.raises(OandaError, match="401"):
                client._request("GET", "/test")
        assert mock_session.request.call_count == 1

    def test_circuit_opens_after_five_terminal_failures(self, client, mock_session):
        # Each 401 records one failure. After 5, the next call raises
        # OandaCircuitOpen without touching the session.
        mock_session.request.side_effect = [_http_error(401) for _ in range(5)]
        with patch("time.sleep"):
            for _ in range(5):
                with pytest.raises(OandaError):
                    client._request("GET", "/test")

        assert client.circuit_breaker.should_reject() is True

        # 6th call should be short-circuited.
        mock_session.request.reset_mock()
        with pytest.raises(OandaCircuitOpen):
            client._request("GET", "/test")
        assert mock_session.request.call_count == 0

    def test_success_resets_circuit(self, client, mock_session):
        # 4 separate failing calls each burn one breaker slot (each
        # call exhausts its own internal 5-retry budget on 5xx and
        # records exactly one failure). Then one successful call
        # clears the trail — the next 4 failures don't trip the
        # breaker even though 8 total failures happened.
        with patch("time.sleep"):
            for _ in range(4):
                mock_session.request.side_effect = [_http_error(500)] * 5
                with pytest.raises(OandaError):
                    client._request("GET", "/test")

            assert client.circuit_breaker.should_reject() is False

            mock_session.request.side_effect = [_http_ok({"ok": True})]
            client._request("GET", "/test")

            # Post-success, fresh failures don't trip.
            for _ in range(4):
                mock_session.request.side_effect = [_http_error(500)] * 5
                with pytest.raises(OandaError):
                    client._request("GET", "/test")

        assert client.circuit_breaker.should_reject() is False


# ─────────────────────────────────────────────────────────────────────
# Account id resolution
# ─────────────────────────────────────────────────────────────────────


class TestAccountResolution:
    def test_call_override_wins(self, client):
        assert (
            client._resolve_account_id("override-id") == "override-id"
        )  # not the client's

    def test_client_default_used(self, client):
        assert client._resolve_account_id(None) == client.account_id

    def test_missing_on_both_raises(self, mock_session):
        # Construct a client with account_id explicitly None + no
        # env fallback available.
        with (
            patch(
                "src.agentfx.adapters.oanda.oanda_api_key",
                return_value="tok",
            ),
            patch(
                "src.agentfx.adapters.oanda.oanda_api_host",
                return_value="api-fxpractice.oanda.com",
            ),
            patch(
                "src.agentfx.adapters.oanda.oanda_account_id",
                return_value="",
            ),
            patch("requests.Session", return_value=mock_session),
        ):
            c = OandaClient()
        with pytest.raises(OandaError, match="account_id required"):
            c._resolve_account_id(None)


# ─────────────────────────────────────────────────────────────────────
# place_market_order
# ─────────────────────────────────────────────────────────────────────


class TestPlaceMarketOrder:
    def test_happy_path_returns_normalised_result(self, client, mock_session):
        oanda_body = {
            "orderCreateTransaction": {"id": "1234"},
            "orderFillTransaction": {
                "id": "1235",
                "price": "1.09342",
                "tradeOpened": {"tradeID": "trade-42", "units": "10000"},
            },
        }
        mock_session.request.return_value = _http_ok(oanda_body)

        result = client.place_market_order("EUR/USD", 10000)

        # v0.6.1 T1: fill_transaction_id added for the reconciler to
        # cross-reference ORDER_FILL events back to Trade rows.
        # v0.6.1 (Wilson 2026-07-16): financing + commission also
        # returned so entries with on-fill commission are captured.
        assert result == {
            "order_id": "1234",
            "trade_id": "trade-42",
            "fill_transaction_id": "1235",
            "fill_price": 1.09342,
            "units": 10000,
            "financing": 0.0,
            "commission": 0.0,
        }
        call = mock_session.request.call_args
        # Body must serialize `units` as a string per OANDA convention.
        assert call.kwargs["json"]["order"]["units"] == "10000"
        assert call.kwargs["json"]["order"]["type"] == "MARKET"
        assert call.kwargs["json"]["order"]["instrument"] == "EUR_USD"

    def test_stop_loss_and_take_profit_forwarded(self, client, mock_session):
        # v0.6.1: adapter raises OandaOrderRejected on empty fill so
        # we need a non-empty fill body here to reach the assertion.
        mock_session.request.return_value = _http_ok(
            {
                "orderCreateTransaction": {"id": "1"},
                "orderFillTransaction": {
                    "id": "2",
                    "price": "1.10",
                    "tradeOpened": {"tradeID": "t1", "units": "5000"},
                },
            }
        )
        client.place_market_order(
            "EUR_USD", 5000, stop_loss_pips=20, take_profit_pips=40
        )
        order = mock_session.request.call_args.kwargs["json"]["order"]
        assert "stopLossOnFill" in order
        assert "takeProfitOnFill" in order
        # 20 pips on EUR_USD = 0.0020
        assert order["stopLossOnFill"]["distance"] == "0.00200"
        # 40 pips on EUR_USD = 0.0040
        assert order["takeProfitOnFill"]["distance"] == "0.00400"

    def test_reject_transaction_raises(self, client, mock_session):
        """v0.6.1: OANDA responds 200 with orderRejectTransaction when
        an order is business-logic-rejected (INSUFFICIENT_MARGIN,
        MARKET_HALTED, etc.). Adapter must surface as
        OandaOrderRejected — silent-fail-with-fake-fill is what caused
        Wilson's $504 hidden loss on 2026-07-15."""
        from src.agentfx.adapters.oanda import OandaOrderRejected

        mock_session.request.return_value = _http_ok(
            {
                "orderRejectTransaction": {
                    "id": "1234",
                    "rejectReason": "INSUFFICIENT_MARGIN",
                    "type": "MARKET_ORDER_REJECT",
                }
            }
        )
        with pytest.raises(OandaOrderRejected) as excinfo:
            client.place_market_order("EUR_USD", 10000)
        assert excinfo.value.reject_reason == "INSUFFICIENT_MARGIN"
        assert excinfo.value.transaction_id == "1234"

    def test_no_fill_returned_raises(self, client, mock_session):
        """Order accepted but returned no fill transaction (e.g., FOK
        couldn't execute at the quoted spread). Adapter must not
        return a phantom fill."""
        from src.agentfx.adapters.oanda import OandaOrderRejected

        mock_session.request.return_value = _http_ok(
            {"orderCreateTransaction": {"id": "9"}}
        )
        with pytest.raises(OandaOrderRejected) as excinfo:
            client.place_market_order("EUR_USD", 10000)
        assert excinfo.value.reject_reason == "NO_FILL_TRANSACTION_RETURNED"

    def test_stop_loss_uses_pip_size_for_jpy(self, client, mock_session):
        mock_session.request.return_value = _http_ok(
            {
                "orderCreateTransaction": {"id": "1"},
                "orderFillTransaction": {
                    "id": "2",
                    "price": "150.00",
                    "tradeOpened": {"tradeID": "t1", "units": "-10000"},
                },
            }
        )
        client.place_market_order("USD_JPY", -10000, stop_loss_pips=15)
        order = mock_session.request.call_args.kwargs["json"]["order"]
        # 15 pips on JPY pair = 0.15
        assert order["stopLossOnFill"]["distance"] == "0.15000"

    def test_signed_units_carried_through(self, client, mock_session):
        mock_session.request.return_value = _http_ok(
            {
                "orderCreateTransaction": {"id": "1"},
                "orderFillTransaction": {
                    "id": "2",
                    "price": "1.10",
                    "tradeOpened": {"tradeID": "t1", "units": "-3000"},
                },
            }
        )
        client.place_market_order("EUR_USD", -3000)
        assert (
            mock_session.request.call_args.kwargs["json"]["order"]["units"] == "-3000"
        )


# ─────────────────────────────────────────────────────────────────────
# close_trade
# ─────────────────────────────────────────────────────────────────────


class TestCloseTrade:
    def test_close_all(self, client, mock_session):
        mock_session.request.return_value = _http_ok(
            {
                "orderFillTransaction": {
                    "pl": "17.50",
                    "units": "-10000",
                    "price": "1.09475",
                }
            }
        )
        result = client.close_trade("trade-123")
        assert result["realized_pnl"] == 17.5
        assert result["closed_units"] == -10000
        assert result["close_price"] == 1.09475
        # Body
        assert mock_session.request.call_args.kwargs["json"] == {"units": "ALL"}

    def test_partial_close_serializes_int_units(self, client, mock_session):
        mock_session.request.return_value = _http_ok(
            {"orderFillTransaction": {"pl": "0", "units": "-500", "price": "1.10"}}
        )
        client.close_trade("trade-123", units=500)
        assert mock_session.request.call_args.kwargs["json"] == {"units": "500"}


def _position_response(long_units: int = 0, short_units: int = 0) -> MagicMock:
    """GET /accounts/{aid}/positions/{inst} shape."""
    return _http_ok(
        {
            "position": {
                "long": {"units": str(long_units)} if long_units else {"units": "0"},
                "short": {"units": str(short_units)} if short_units else {"units": "0"},
                "unrealizedPL": "0",
            }
        }
    )


def _close_dispatch(position_resp: MagicMock, close_resp: MagicMock):
    """side_effect for mock_session.request that returns the position
    resp on GET and the close resp on PUT — matches the two-call shape
    of `close_all_positions` after the 2026-07-16 preflight fix."""

    def _fn(method, *_args, **_kwargs):
        return position_resp if method == "GET" else close_resp

    return _fn


class TestCloseAllPositions:
    """Wilson UAT 2026-07-16 probe established: OANDA rejects the whole
    request with `CLOSEOUT_POSITION_REJECT` (both sides) when you send
    `{longUnits: ALL, shortUnits: ALL}` while only one side has units.
    The adapter now preflights `get_position` and only includes sides
    with actual exposure."""

    def test_only_long_open_sends_only_long_units(self, client, mock_session):
        position = _position_response(long_units=10000, short_units=0)
        close = _http_ok(
            {"longOrderFillTransaction": {"pl": "3.00", "units": "-10000"}}
        )
        mock_session.request.side_effect = _close_dispatch(position, close)
        r = client.close_all_positions("EUR_USD")
        assert r["long_realized_pnl"] == 3.0
        assert r["short_realized_pnl"] == 0.0
        # Second call is the PUT with only longUnits — the whole point
        # of this fix. shortUnits must not be present.
        put_call = mock_session.request.call_args_list[1]
        assert put_call.args[0] == "PUT"
        assert put_call.kwargs["json"] == {"longUnits": "ALL"}

    def test_only_short_open_sends_only_short_units(self, client, mock_session):
        position = _position_response(long_units=0, short_units=-5000)
        close = _http_ok(
            {"shortOrderFillTransaction": {"pl": "-1.50", "units": "5000"}}
        )
        mock_session.request.side_effect = _close_dispatch(position, close)
        r = client.close_all_positions("EUR_USD")
        assert r["short_realized_pnl"] == -1.5
        assert r["long_realized_pnl"] == 0.0
        put_call = mock_session.request.call_args_list[1]
        assert put_call.kwargs["json"] == {"shortUnits": "ALL"}

    def test_hedged_position_sends_both_sides(self, client, mock_session):
        position = _position_response(long_units=10000, short_units=-4000)
        close = _http_ok(
            {
                "longOrderFillTransaction": {"pl": "3.00", "units": "-10000"},
                "shortOrderFillTransaction": {"pl": "-1.50", "units": "4000"},
            }
        )
        mock_session.request.side_effect = _close_dispatch(position, close)
        r = client.close_all_positions("EUR_USD")
        assert r["long_realized_pnl"] == 3.0
        assert r["short_realized_pnl"] == -1.5
        put_call = mock_session.request.call_args_list[1]
        assert put_call.kwargs["json"] == {"longUnits": "ALL", "shortUnits": "ALL"}

    def test_flat_position_is_no_op_no_put(self, client, mock_session):
        """Both sides zero → short-circuit, don't hit OANDA at all."""
        mock_session.request.return_value = _position_response(0, 0)
        r = client.close_all_positions("EUR_USD")
        assert r["long_realized_pnl"] == 0.0
        assert r["short_realized_pnl"] == 0.0
        # Only the GET happened.
        assert mock_session.request.call_count == 1
        assert mock_session.request.call_args.args[0] == "GET"

    def test_reject_transaction_in_200_body_raises(self, client, mock_session):
        """OANDA can return 200 with a reject transaction embedded when
        a specific side can't be closed (POSITION_LOCKED,
        INSUFFICIENT_MARGIN). Adapter surfaces as OandaOrderRejected."""
        from src.agentfx.adapters.oanda import OandaOrderRejected

        position = _position_response(long_units=0, short_units=-5000)
        close = _http_ok(
            {
                "shortOrderRejectTransaction": {
                    "id": "999",
                    "rejectReason": "POSITION_LOCKED",
                    "type": "MARKET_ORDER_REJECT",
                }
            }
        )
        mock_session.request.side_effect = _close_dispatch(position, close)
        with pytest.raises(OandaOrderRejected) as excinfo:
            client.close_all_positions("EUR_USD")
        assert excinfo.value.reject_reason == "POSITION_LOCKED"
        assert excinfo.value.transaction_id == "999"

    def test_race_between_get_and_put_swallowed(self, client, mock_session):
        """Position closes (SL/TP fill) between our GET and PUT →
        OANDA returns CLOSEOUT_POSITION_DOESNT_EXIST 4xx. From the
        runner's perspective the position is flat, which is what it
        wanted — treat as zero-fill, don't raise."""
        position = _position_response(long_units=10000, short_units=0)
        error_resp = _http_error(
            400,
            '{"errorMessage":"The Position specified does not exist",'
            '"errorCode":"CLOSEOUT_POSITION_DOESNT_EXIST"}',
        )
        mock_session.request.side_effect = _close_dispatch(position, error_resp)
        r = client.close_all_positions("EUR_USD")
        assert r["long_realized_pnl"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# Inspection endpoints
# ─────────────────────────────────────────────────────────────────────


class TestGetPosition:
    def test_extracts_net_units(self, client, mock_session):
        mock_session.request.return_value = _http_ok(
            {
                "position": {
                    "long": {"units": "10000", "averagePrice": "1.09342"},
                    "short": {"units": "-3000", "averagePrice": "1.10012"},
                    "unrealizedPL": "44.20",
                }
            }
        )
        p = client.get_position("EUR_USD")
        assert p["long_units"] == 10000
        assert p["short_units"] == -3000
        # net = 10000 + (-3000) = 7000
        assert p["net_units"] == 7000
        assert p["unrealized_pnl"] == 44.2
        assert p["avg_long_price"] == 1.09342
        assert p["avg_short_price"] == 1.10012


class TestGetAccountBalance:
    def test_shape(self, client, mock_session):
        mock_session.request.return_value = _http_ok(
            {
                "account": {
                    "balance": "10000.00",
                    "NAV": "10044.20",
                    "marginUsed": "500.00",
                    "marginAvailable": "9500.00",
                    "currency": "USD",
                    "unrealizedPL": "44.20",
                }
            }
        )
        b = client.get_account_balance()
        assert b == {
            "balance": 10000.0,
            "nav": 10044.20,
            "margin_used": 500.0,
            "margin_available": 9500.0,
            "currency": "USD",
            "unrealized_pnl": 44.20,
        }


class TestGetOpenTrades:
    def test_normalises_types(self, client, mock_session):
        mock_session.request.return_value = _http_ok(
            {
                "trades": [
                    {
                        "id": "trade-1",
                        "instrument": "EUR_USD",
                        "currentUnits": "10000",
                        "price": "1.09342",
                        "unrealizedPL": "44.20",
                        "openTime": "2026-07-05T13:04:12.000000000Z",
                    }
                ]
            }
        )
        trades = client.get_open_trades()
        assert len(trades) == 1
        t = trades[0]
        assert t["trade_id"] == "trade-1"
        assert t["units"] == 10000  # int cast
        assert t["open_price"] == 1.09342
        assert t["unrealized_pnl"] == 44.20


class TestGetPricing:
    def test_extracts_bid_ask_spread(self, client, mock_session):
        mock_session.request.return_value = _http_ok(
            {
                "prices": [
                    {
                        "instrument": "EUR_USD",
                        "bids": [{"price": "1.09340"}],
                        "asks": [{"price": "1.09348"}],
                        "time": "2026-07-05T13:04:12Z",
                    }
                ]
            }
        )
        prices = client.get_pricing("EUR_USD")
        assert len(prices) == 1
        assert prices[0]["bid"] == 1.09340
        assert prices[0]["ask"] == 1.09348
        # 8 pips spread; comparison uses approx to avoid FP noise.
        assert prices[0]["spread"] == pytest.approx(0.00008, abs=1e-6)

    def test_accepts_multiple_instruments(self, client, mock_session):
        mock_session.request.return_value = _http_ok({"prices": []})
        client.get_pricing(["EUR_USD", "USD_JPY"])
        params = mock_session.request.call_args.kwargs["params"]
        assert params["instruments"] == "EUR_USD,USD_JPY"
