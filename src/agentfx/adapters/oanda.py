from __future__ import annotations

import time
from datetime import UTC, datetime

import requests  # type: ignore[import-untyped]

from agentfx.shared.config import oanda_account_id, oanda_api_host, oanda_api_key

ISO8601 = "%Y-%m-%dT%H:%M:%S.%fZ"


class OandaError(Exception):
    pass


def to_oanda_instrument(symbol: str) -> str:
    s = symbol.strip().upper().replace("/", "_")
    if "_" not in s and len(s) == 6:
        s = f"{s[:3]}_{s[3:]}"
    return s


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

    def _request(self, method: str, path: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}{path}"
        for attempt in range(5):
            try:
                resp = self.session.request(
                    method, url, params=params, timeout=self.timeout
                )
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as e:
                if attempt < 4:
                    time.sleep(1 + attempt)
                    continue
                raise OandaError(f"connection_error: {e}") from e
            if resp.status_code == 429:
                time.sleep(1 + attempt)
                continue
            if resp.status_code >= 400:
                raise OandaError(f"{resp.status_code} {resp.text}")
            return resp.json()
        raise OandaError("Rate limited repeatedly by OANDA")

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
