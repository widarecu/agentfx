from __future__ import annotations

import random
import time
from collections import deque
from datetime import UTC, datetime

import requests  # type: ignore[import-untyped]

from agentfx.shared.config import oanda_account_id, oanda_api_host, oanda_api_key

ISO8601 = "%Y-%m-%dT%H:%M:%S.%fZ"


class OandaError(Exception):
    """Base exception for any OANDA v20 REST failure."""


class OandaCircuitOpen(OandaError):
    """Raised when the per-client circuit breaker is open.

    Signals that too many consecutive failures have occurred in a
    short window; the paper runner treats this as
    `stop_reason=BROKER_CIRCUIT` and halts the run so a broken broker
    doesn't burn account margin trying to place doomed orders.
    """


class OandaCircuitBreaker:
    """Per-client failure gate (US-020).

    Trips OPEN after `threshold` failures inside a rolling
    `window_seconds` interval. Any success resets the failure trail.
    Once OPEN, `should_reject()` returns True until the window has
    elapsed since the last failure — no half-open state; the runner
    catches `OandaCircuitOpen` and stops the run.

    Kept as a plain class (no `functools.lru_cache`, no globals) so
    each `OandaClient` has its own breaker — one user's OANDA outage
    doesn't trip another user's client.
    """

    def __init__(self, threshold: int = 5, window_seconds: float = 60.0) -> None:
        self.threshold = threshold
        self.window_seconds = window_seconds
        self._failures: deque[float] = deque()

    def record_failure(self) -> None:
        self._failures.append(time.monotonic())
        # Drop stale entries so `should_reject` sees only the current
        # window. Done here rather than lazily so the deque doesn't
        # grow unbounded on a client that never checks state.
        cutoff = time.monotonic() - self.window_seconds
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

    def record_success(self) -> None:
        self._failures.clear()

    def should_reject(self) -> bool:
        cutoff = time.monotonic() - self.window_seconds
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()
        return len(self._failures) >= self.threshold


def _backoff_delay_seconds(
    attempt: int,
    *,
    base_ms: int = 500,
    cap_ms: int = 8000,
) -> float:
    """Exponential backoff with 25% jitter for retryable requests.

    `attempt` starts at 0 for the first retry, so the sequence is
    ~0.5s, ~1s, ~2s, ~4s, ~8s (capped). Jitter is uniform in
    `[0, 0.25 * base_delay]` — the standard "decorrelated jitter"
    variant would be denser here but adds complexity without changing
    the correctness properties we care about (bounded, non-zero,
    non-uniform-across-clients).
    """
    base = min(cap_ms, base_ms * (2**attempt))
    jitter = random.uniform(0, base * 0.25)
    return (base + jitter) / 1000.0


def to_oanda_instrument(symbol: str) -> str:
    s = symbol.strip().upper().replace("/", "_")
    if "_" not in s and len(s) == 6:
        s = f"{s[:3]}_{s[3:]}"
    return s


def _pips_to_price_distance(instrument: str, pips: float) -> str:
    """Convert a pip distance to the OANDA v20 `distance` string.

    OANDA's pip location varies by quote currency: 1 pip = 0.0001 for
    most majors, 0.01 for JPY pairs. We resolve that here so the
    caller (paper runner) can think in pips without knowing the
    quirk. Returned as a str because the OANDA API expects
    string-quoted decimals for money fields.

    Not a full replacement for OANDA's per-instrument pipLocation
    metadata — for the v0.6.0 majors set (EUR/GBP/AUD/USD_JPY etc.)
    the two-tier heuristic is exact. If we broaden to exotics later
    we'd fetch pipLocation from `/v3/accounts/{id}/instruments`
    on client init.
    """
    _, quote = instrument.split("_")
    pip_size = 0.01 if quote == "JPY" else 0.0001
    return f"{pips * pip_size:.5f}"


#: Mapping from user-supplied environment name to OANDA v20 REST host.
#: Kept explicit so a typo in `environment` fails at client construction
#: rather than pointing at the wrong endpoint.
_OANDA_ENV_TO_HOST: dict[str, str] = {
    "practice": "api-fxpractice.oanda.com",
    "live": "api-fxtrade.oanda.com",
}


class OandaClient:
    def __init__(
        self,
        api_key: str | None = None,
        host: str | None = None,
        account_id: str | None = None,
        environment: str | None = None,
        timeout: int = 15,
    ):
        """Build a client bound to one OANDA identity.

        The v0.6.0 paper runner (US-021) passes `api_key` and
        `environment` explicitly from a user's decrypted credentials —
        the app-wide fallbacks (`oanda_api_key()` etc.) only fire when
        NO explicit args are given, so the shared candle ingestion path
        keeps working unchanged.

        `account_id` is optional. The single-account fallback from the
        shared config is kept for backwards compatibility with the
        candle ingestion worker; per-user code paths pass the
        `account_id` per request via the list-accounts → picker →
        runner flow.
        """
        self.api_key = api_key or oanda_api_key()
        # `environment` wins over `host` when both are set — matches
        # the v0.6.0 mental model where callers think in "practice /
        # live" not in hostnames. Unknown strings raise.
        if environment is not None:
            env = environment.lower()
            if env not in _OANDA_ENV_TO_HOST:
                raise OandaError(
                    f"unknown OANDA environment '{environment}' "
                    f"(expected one of {list(_OANDA_ENV_TO_HOST.keys())})"
                )
            self.host = _OANDA_ENV_TO_HOST[env]
            self.environment = env
        else:
            self.host = host or oanda_api_host()
            # Derive so callers can safely log `client.environment`.
            self.environment = "practice" if "fxpractice" in self.host else "live"
        self.account_id = account_id or oanda_account_id()
        if not self.api_key:
            raise OandaError("OANDA_API_KEY is not set")
        self.base_url = f"https://{self.host}/v3"
        self.timeout = timeout
        # Per-instance circuit breaker so one user's OANDA outage
        # doesn't trip another user's client. 5 failures / 60s window
        # matches the paper runner's tolerance in US-023.
        self.circuit_breaker = OandaCircuitBreaker(threshold=5, window_seconds=60.0)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )

    # ─────────────────────────────────────────────────────────────
    # v0.6.0 (US-019) — account discovery for the credentials flow.
    # ─────────────────────────────────────────────────────────────

    def list_accounts(self) -> list[dict]:
        """Return the sub-accounts this API key can access.

        Hits `GET /v3/accounts`. The Bearer token in `self.api_key`
        identifies the OANDA login; the response is a list of accounts
        (`id`, optional `tags`) that we normalise into `{account_id,
        alias, currency, balance, margin_available, currency}` by
        pairing each id with a follow-up `/accounts/{id}/summary`.

        This is the minimal shape the credentials verify endpoint (and
        later the deploy modal) needs. Per-account balance is queried
        lazily — the caller decides whether to fetch it now (for a
        picker) or later (for the run detail 5s polling).
        """
        list_resp = self._request("GET", "/accounts", params={})
        raw_accounts = list_resp.get("accounts", []) or []
        result: list[dict] = []
        for acct in raw_accounts:
            account_id = acct.get("id")
            if not account_id:
                continue
            # Per OANDA v20 REST, the summary is the cheapest endpoint
            # that gives alias + currency + balance in one shot. Errors
            # here surface as OandaError from `_request`; callers wrap.
            summary_resp = self._request(
                "GET", f"/accounts/{account_id}/summary", params={}
            )
            summary = summary_resp.get("account", {})
            result.append(
                {
                    "account_id": str(account_id),
                    "alias": summary.get("alias") or None,
                    "currency": summary.get("currency") or "USD",
                    # Balance / margin are informational only. The
                    # credentials verify endpoint doesn't persist
                    # these — it just echoes them so the frontend can
                    # render the detected-accounts table without a
                    # follow-up round-trip. Cast to float defensively;
                    # OANDA returns as strings.
                    "balance": float(summary.get("balance", 0) or 0),
                    "margin_available": float(summary.get("marginAvailable", 0) or 0),
                }
            )
        return result

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """Issue a v20 REST call with retry + circuit-breaker semantics.

        Retryable failures (network errors, HTTP 429, HTTP 5xx) burn
        an attempt and sleep for an exponentially-growing delay with
        jitter. Non-retryable failures (HTTP 4xx other than 429) fail
        immediately — a 401 won't get better by trying again, and the
        paper runner needs a fast signal so the user can fix creds.

        The circuit breaker is checked BEFORE each attempt. Any
        failure that reaches the retry cap records to the breaker;
        any successful response resets it. Once the breaker trips,
        the next call raises `OandaCircuitOpen` and the runner
        halts.
        """
        if self.circuit_breaker.should_reject():
            raise OandaCircuitOpen(
                "OANDA circuit breaker open — "
                f"{self.circuit_breaker.threshold} failures within "
                f"{self.circuit_breaker.window_seconds:.0f}s"
            )

        url = f"{self.base_url}{path}"
        params = params or {}
        last_error: Exception | None = None

        for attempt in range(5):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    timeout=self.timeout,
                )
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as e:
                last_error = e
                if attempt < 4:
                    time.sleep(_backoff_delay_seconds(attempt))
                    continue
                self.circuit_breaker.record_failure()
                raise OandaError(f"connection_error: {e}") from e

            # Retry-eligible responses. 429 is explicit rate-limit;
            # 5xx is server-side transient. Both burn an attempt and
            # back off before the next try.
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = OandaError(f"{resp.status_code} {resp.text[:200]}")
                if attempt < 4:
                    time.sleep(_backoff_delay_seconds(attempt))
                    continue
                self.circuit_breaker.record_failure()
                raise last_error

            # Non-retryable client errors — surface immediately.
            if resp.status_code >= 400:
                self.circuit_breaker.record_failure()
                raise OandaError(f"{resp.status_code} {resp.text[:500]}")

            # Success — clear the breaker.
            self.circuit_breaker.record_success()
            # Some OANDA responses (order fills at the exact edge of
            # a rejection) return 200 with an empty body; be defensive.
            if not resp.content:
                return {}
            return resp.json()

        # Exhausted attempts — record failure and raise the last error.
        self.circuit_breaker.record_failure()
        if last_error is not None:
            raise (
                last_error
                if isinstance(last_error, OandaError)
                else OandaError(str(last_error))
            )
        raise OandaError("Rate limited repeatedly by OANDA")

    def _resolve_account_id(self, account_id: str | None) -> str:
        """Pick the account id to use for a per-account call.

        Per-call `account_id` wins; falls back to the one wired at
        construction. Missing on both surfaces as an `OandaError` so
        the runner can't accidentally hit /positions on a client that
        has no account context.
        """
        aid = account_id or self.account_id
        if not aid:
            raise OandaError(
                "account_id required — pass explicitly or set at "
                "OandaClient construction"
            )
        return aid

    # ─────────────────────────────────────────────────────────────
    # v0.6.0 (US-020) — order execution.
    # ─────────────────────────────────────────────────────────────

    def place_market_order(
        self,
        instrument: str,
        units: int,
        *,
        stop_loss_pips: float | None = None,
        take_profit_pips: float | None = None,
        account_id: str | None = None,
    ) -> dict:
        """Place a market order.

        `units` is signed OANDA convention — positive = long, negative
        = short. Stop-loss / take-profit are optional and specified in
        pips relative to the fill price; the client computes the price
        levels from the fill so the caller doesn't need to know pip
        location.

        Returns
        -------
        dict with keys:
            - `order_id`: transaction id of the create
            - `trade_id`: id of the trade opened by the fill (or None if
              the order failed to fill — e.g. spread went outside the
              guaranteed range)
            - `fill_price`: fill price as a float
            - `units`: units actually filled (signed)
        """
        aid = self._resolve_account_id(account_id)
        instrument_norm = to_oanda_instrument(instrument)

        order: dict = {
            "type": "MARKET",
            "instrument": instrument_norm,
            "units": str(int(units)),
            "positionFill": "DEFAULT",
            "timeInForce": "FOK",
        }
        # Stop-loss / take-profit as ON_FILL order specs. Distance is
        # in price units (not pips) per the OANDA v20 API — we pass
        # `distance` in the account's home price representation, which
        # the OANDA server converts against the fill's mid.
        if stop_loss_pips is not None:
            order["stopLossOnFill"] = {
                "distance": _pips_to_price_distance(instrument_norm, stop_loss_pips),
                "timeInForce": "GTC",
            }
        if take_profit_pips is not None:
            order["takeProfitOnFill"] = {
                "distance": _pips_to_price_distance(instrument_norm, take_profit_pips),
                "timeInForce": "GTC",
            }

        resp = self._request(
            "POST",
            f"/accounts/{aid}/orders",
            json_body={"order": order},
        )

        create = resp.get("orderCreateTransaction") or {}
        fill = resp.get("orderFillTransaction") or {}
        trade_opened = fill.get("tradeOpened") or {}
        return {
            "order_id": create.get("id"),
            "trade_id": trade_opened.get("tradeID"),
            "fill_price": float(fill["price"]) if "price" in fill else None,
            "units": (
                int(trade_opened["units"]) if "units" in trade_opened else int(units)
            ),
        }

    def close_trade(
        self,
        trade_id: str,
        units: str | int = "ALL",
        *,
        account_id: str | None = None,
    ) -> dict:
        """Close (or partially close) a trade by its OANDA trade id.

        Returns the realized P/L of the close as reported by OANDA.
        """
        aid = self._resolve_account_id(account_id)
        body: dict = {"units": str(units) if not isinstance(units, str) else units}
        resp = self._request(
            "PUT",
            f"/accounts/{aid}/trades/{trade_id}/close",
            json_body=body,
        )
        fill = resp.get("orderFillTransaction") or {}
        return {
            "trade_id": trade_id,
            "realized_pnl": float(fill["pl"]) if "pl" in fill else 0.0,
            "closed_units": int(fill["units"]) if "units" in fill else 0,
            "close_price": float(fill["price"]) if "price" in fill else None,
        }

    def close_all_positions(
        self,
        instrument: str,
        *,
        account_id: str | None = None,
    ) -> dict:
        """Flatten both sides of an instrument position.

        OANDA's v20 API distinguishes long and short — a hedged
        instrument can have both open at once. We close both in a
        single request.

        Reality-check (Wilson UAT 2026-07-15): the earlier docstring
        claimed OANDA silently skips empty sides. Wrong. OANDA returns
        HTTP 400 with `CLOSEOUT_POSITION_DOESNT_EXIST` when neither
        side has exposure. That's not an error from our perspective —
        we asked "make sure it's flat" and it is flat — so we swallow
        this specific rejection and return a zero-fill result.
        """
        aid = self._resolve_account_id(account_id)
        instrument_norm = to_oanda_instrument(instrument)
        try:
            resp = self._request(
                "PUT",
                f"/accounts/{aid}/positions/{instrument_norm}/close",
                json_body={"longUnits": "ALL", "shortUnits": "ALL"},
            )
        except OandaError as exc:
            # Only swallow the "nothing to close" case; any other 4xx/5xx
            # is a real problem the caller must see.
            if "CLOSEOUT_POSITION_DOESNT_EXIST" in str(exc):
                return {
                    "long_realized_pnl": 0.0,
                    "short_realized_pnl": 0.0,
                    "long_units_closed": 0,
                    "short_units_closed": 0,
                }
            raise
        long_fill = resp.get("longOrderFillTransaction") or {}
        short_fill = resp.get("shortOrderFillTransaction") or {}
        return {
            "long_realized_pnl": float(long_fill["pl"]) if "pl" in long_fill else 0.0,
            "short_realized_pnl": (
                float(short_fill["pl"]) if "pl" in short_fill else 0.0
            ),
            "long_units_closed": int(long_fill.get("units", 0)),
            "short_units_closed": int(short_fill.get("units", 0)),
        }

    # ─────────────────────────────────────────────────────────────
    # v0.6.0 (US-020) — account + position inspection.
    # ─────────────────────────────────────────────────────────────

    def get_position(
        self,
        instrument: str,
        *,
        account_id: str | None = None,
    ) -> dict:
        """Return the current position for one instrument.

        Both sides may be non-zero on OANDA (hedging accounts). The
        paper runner uses this to compute the net exposure it needs
        to place a signal-driven order.
        """
        aid = self._resolve_account_id(account_id)
        instrument_norm = to_oanda_instrument(instrument)
        resp = self._request(
            "GET",
            f"/accounts/{aid}/positions/{instrument_norm}",
        )
        position = resp.get("position") or {}
        long_side = position.get("long") or {}
        short_side = position.get("short") or {}
        long_units = int(long_side.get("units", 0) or 0)
        short_units = int(short_side.get("units", 0) or 0)
        return {
            "instrument": instrument_norm,
            "long_units": long_units,
            "short_units": short_units,
            "net_units": long_units + short_units,  # short_units is negative
            "unrealized_pnl": float(position.get("unrealizedPL", 0) or 0),
            "avg_long_price": (
                float(long_side["averagePrice"])
                if long_side.get("averagePrice")
                else None
            ),
            "avg_short_price": (
                float(short_side["averagePrice"])
                if short_side.get("averagePrice")
                else None
            ),
        }

    def get_account_balance(
        self,
        *,
        account_id: str | None = None,
    ) -> dict:
        """Return the caller's live balance summary.

        Used by the paper-runner snapshot (US-021) at run start and
        the run-detail 5s polling for the live-vs-snapshot P&L card.
        """
        aid = self._resolve_account_id(account_id)
        resp = self._request("GET", f"/accounts/{aid}/summary")
        account = resp.get("account") or {}
        return {
            "balance": float(account.get("balance", 0) or 0),
            "nav": float(account.get("NAV", 0) or 0),
            "margin_used": float(account.get("marginUsed", 0) or 0),
            "margin_available": float(account.get("marginAvailable", 0) or 0),
            "currency": account.get("currency") or "USD",
            "unrealized_pnl": float(account.get("unrealizedPL", 0) or 0),
        }

    def get_open_trades(
        self,
        *,
        account_id: str | None = None,
    ) -> list[dict]:
        """List the account's currently-open trades.

        Shape mirrors OANDA's — id, instrument, units, price, unrealized
        PL — but casts to native Python types so the runner doesn't
        have to reparse strings.
        """
        aid = self._resolve_account_id(account_id)
        resp = self._request("GET", f"/accounts/{aid}/openTrades")
        result: list[dict] = []
        for raw in resp.get("trades") or []:
            result.append(
                {
                    "trade_id": raw.get("id"),
                    "instrument": raw.get("instrument"),
                    "units": int(raw.get("currentUnits", 0) or 0),
                    "open_price": float(raw.get("price", 0) or 0),
                    "unrealized_pnl": float(raw.get("unrealizedPL", 0) or 0),
                    "open_time": raw.get("openTime"),
                }
            )
        return result

    def get_pricing(
        self,
        instruments: list[str] | str,
        *,
        account_id: str | None = None,
    ) -> list[dict]:
        """Return the current bid/ask/spread for one or more instruments.

        The paper runner queries this on every decision-loop tick to
        cost-check a candidate order (avoid firing when spread has
        widened past the expected cost model).
        """
        aid = self._resolve_account_id(account_id)
        if isinstance(instruments, str):
            instruments = [instruments]
        instrument_norm = ",".join(to_oanda_instrument(i) for i in instruments)
        resp = self._request(
            "GET",
            f"/accounts/{aid}/pricing",
            params={"instruments": instrument_norm},
        )
        prices: list[dict] = []
        for raw in resp.get("prices") or []:
            bids = raw.get("bids") or []
            asks = raw.get("asks") or []
            bid = float(bids[0]["price"]) if bids else 0.0
            ask = float(asks[0]["price"]) if asks else 0.0
            prices.append(
                {
                    "instrument": raw.get("instrument"),
                    "bid": bid,
                    "ask": ask,
                    "spread": ask - bid,
                    "time": raw.get("time"),
                }
            )
        return prices

    def get_candles(
        self,
        instrument: str,
        granularity: str = "M1",
        price: str = "M",
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        count: int | None = None,
        include_first: bool = False,
    ) -> list[dict]:
        instrument = to_oanda_instrument(instrument)
        params: dict[str, str] = {
            "granularity": granularity,
            "price": price,
        }
        if count is not None:
            params["count"] = str(count)
        if from_ts is not None:
            params["from"] = from_ts.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if to_ts is not None:
            params["to"] = to_ts.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if include_first:
            params["includeFirst"] = "true"
        data = self._request("GET", f"/instruments/{instrument}/candles", params)
        candles = data.get("candles", [])
        norm = []
        for c in candles:
            t = c["time"]
            vol = c.get("volume", 0)
            complete = c.get("complete", False)
            if price.upper() == "M" and "mid" in c:
                o = c["mid"]["o"]
                h = c["mid"]["h"]
                lo = c["mid"]["l"]
                cl = c["mid"]["c"]
            elif "bid" in c and "ask" in c:
                o = (float(c["bid"]["o"]) + float(c["ask"]["o"])) / 2
                h = (float(c["bid"]["h"]) + float(c["ask"]["h"])) / 2
                lo = (float(c["bid"]["l"]) + float(c["ask"]["l"])) / 2
                cl = (float(c["bid"]["c"]) + float(c["ask"]["c"])) / 2
            else:
                # Skip malformed candle
                continue
            norm.append(
                {
                    "time": t,
                    "open": float(o),
                    "high": float(h),
                    "low": float(lo),
                    "close": float(cl),
                    "volume": int(vol),
                    "complete": bool(complete),
                }
            )
        return norm

    def get_instrument_financing(self, instrument: str) -> dict:
        """Fetch current long/short financing rates from OANDA.

        Calls GET /v3/accounts/{accountID}/instruments?instruments={instrument}
        and returns the per-day financing rates that OANDA charges or credits
        when a position is held past the daily rollover (21:00 UTC for
        OANDA AU accounts).

        Returns:
            dict with keys:
              - long_rate: annualised decimal (positive = client pays)
              - short_rate: annualised decimal (positive = client pays)
              - fetched_at: datetime in UTC
        """
        if not self.account_id:
            raise OandaError("OANDA_ACCOUNT_ID is not set")
        instrument = to_oanda_instrument(instrument)
        params = {"instruments": instrument}
        path = f"/accounts/{self.account_id}/instruments"
        data = self._request("GET", path, params)
        instruments = data.get("instruments", [])
        if not instruments:
            raise OandaError(f"OANDA returned no metadata for {instrument}")
        info = instruments[0]
        financing = info.get("financing", {}) or {}
        long_rate_raw = financing.get("longRate")
        short_rate_raw = financing.get("shortRate")
        if long_rate_raw is None or short_rate_raw is None:
            raise OandaError(
                f"OANDA response missing financing rates for {instrument}: {info}"
            )
        return {
            "long_rate": float(long_rate_raw),
            "short_rate": float(short_rate_raw),
            "fetched_at": datetime.now(UTC),
        }

    def get_instrument_spread(self, instrument: str) -> dict:
        """Snapshot the current bid/ask spread for an instrument.

        Calls GET /v3/accounts/{accountID}/pricing?instruments={instrument}
        and returns the spread (ask − bid) converted to pips. This is the
        actual cost OANDA is charging *at the moment of this call*; for a
        more representative "typical" value, call during liquid hours
        (London/NY overlap is ideal) or sample multiple times and take
        the median.

        Pip size is inferred from the instrument symbol: 0.01 for JPY
        crosses, 0.0001 otherwise — matches the convention in
        `services/backtest.py:pip_size_for_pair`.

        Raises OandaError if the market is closed (no prices available)
        or the response is malformed.

        Returns:
            dict with keys:
              - spread_pips: float
              - bid: float (closing bid at snapshot)
              - ask: float (closing ask at snapshot)
              - fetched_at: datetime in UTC
        """
        if not self.account_id:
            raise OandaError("OANDA_ACCOUNT_ID is not set")
        oanda_instrument = to_oanda_instrument(instrument)
        params = {"instruments": oanda_instrument}
        path = f"/accounts/{self.account_id}/pricing"
        data = self._request("GET", path, params)
        prices = data.get("prices", [])
        if not prices:
            raise OandaError(f"OANDA returned no pricing for {oanda_instrument}")
        price = prices[0]
        if not price.get("tradeable", True):
            raise OandaError(
                f"{oanda_instrument} not tradeable right now "
                "(likely closed market) — re-run during liquid hours"
            )
        bids = price.get("bids") or []
        asks = price.get("asks") or []
        if not bids or not asks:
            raise OandaError(
                f"OANDA pricing response missing bids/asks for {oanda_instrument}"
            )
        # OANDA returns price ladders; take the top-of-book (best bid, best ask).
        best_bid = float(bids[0]["price"])
        best_ask = float(asks[0]["price"])
        pip_size = 0.01 if oanda_instrument.endswith("_JPY") else 0.0001
        spread_pips = (best_ask - best_bid) / pip_size
        return {
            "spread_pips": float(spread_pips),
            "bid": best_bid,
            "ask": best_ask,
            "fetched_at": datetime.now(UTC),
        }
