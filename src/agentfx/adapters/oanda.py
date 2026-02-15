from __future__ import annotations

import time
from datetime import UTC, datetime

import requests  # type: ignore[import-untyped]

from agentfx.shared.config import oanda_api_host, oanda_api_key

ISO8601 = "%Y-%m-%dT%H:%M:%S.%fZ"


class OandaError(Exception):
    pass


def to_oanda_instrument(symbol: str) -> str:
    s = symbol.strip().upper().replace("/", "_")
    if "_" not in s and len(s) == 6:
        s = f"{s[:3]}_{s[3:]}"
    return s


class OandaClient:
    def __init__(
        self,
        api_key: str | None = None,
        host: str | None = None,
        timeout: int = 15,
    ):
        self.api_key = api_key or oanda_api_key()
        self.host = host or oanda_api_host()
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
