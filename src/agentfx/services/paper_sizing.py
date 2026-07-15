"""Currency-accurate position sizing for the paper runner (US-024/v0.6.1).

Backtest already sizes each trade from `spec.size.risk_pct × balance`
(see `backtest.py::_dsl_position_size`) but assumes a USD account and
converts pip values via `_pip_value_usd`. The paper runner needs the
same formula PLUS awareness of the account's actual currency —
Wilson's staging Practice account is AUD-denominated, so a naïve
USD calc under-sizes trades by the AUD/USD rate (~30% at 0.66).

`compute_position_units` returns the integer unit count for a fresh
entry given the strategy DSL, the current OANDA balance, and the
account's currency. It's called by `_signal_tick` on every fresh
entry event.

Kept in its own module so unit tests can drive it with plain floats
+ a fake client, without pulling in the whole runner subprocess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SizingBreakdown:
    """Trace of one sizing computation for the decision log.

    All amounts are in the account's home currency. `notes` carries
    any warnings (e.g., 'capped at safety limit').
    """

    balance: float
    risk_pct: float
    stop_pips: float
    pip_value_account: float
    account_currency: str
    quote_to_account_rate: float
    desired_units: int
    capped_units: int
    safety_cap: int | None
    notes: str


def _pip_size_for_pair(instrument: str) -> float:
    """0.01 for JPY-quoted pairs, 0.0001 for everything else.

    Duplicated intentionally rather than importing from paper_runner
    to keep this module's dependency graph flat (paper_runner imports
    from here, not the other way around).
    """
    parts = instrument.upper().replace("/", "_").split("_")
    if len(parts) != 2:
        raise ValueError(f"unsupported instrument symbol: {instrument}")
    _, quote = parts
    return 0.01 if quote == "JPY" else 0.0001


def _pip_value_in_quote(instrument: str) -> float:
    """Value of 1 pip per 1 unit in the instrument's QUOTE currency."""
    return _pip_size_for_pair(instrument)


def _quote_to_account_rate(
    *,
    quote_currency: str,
    account_currency: str,
    client,
) -> float:
    """Return how many units of `account_currency` equal 1 unit of
    `quote_currency`, using OANDA's live pricing.

    Same currency → 1.0 (no conversion).
    OANDA lists FX pairs in only one direction; we try the natural
    `{quote}_{account}` first, then fall back to `{account}_{quote}`
    inverted. Raises `ValueError` if neither is tradable — caller
    should treat that as a sizing failure and skip the trade rather
    than guessing.
    """
    if quote_currency == account_currency:
        return 1.0

    # Try quote_account (e.g., USD_AUD)
    try:
        primary = f"{quote_currency}_{account_currency}"
        pricing = client.get_pricing(primary)
        rate = _mid_price(pricing)
        if rate > 0:
            return rate
    except Exception:  # noqa: BLE001
        pass

    # Fall back to inverse (e.g., AUD_USD — the more common OANDA listing)
    try:
        inverse = f"{account_currency}_{quote_currency}"
        pricing = client.get_pricing(inverse)
        rate = _mid_price(pricing)
        if rate > 0:
            return 1.0 / rate
    except Exception:  # noqa: BLE001
        pass

    raise ValueError(
        f"no OANDA pricing available for {quote_currency}<->{account_currency} "
        f"conversion; cannot size trade accurately"
    )


def _mid_price(pricing) -> float:
    """Extract the mid price from OandaClient.get_pricing() output.

    Returns 0.0 on a malformed response so the caller falls through
    to the next lookup attempt.
    """
    if isinstance(pricing, list):
        if not pricing:
            return 0.0
        first = pricing[0]
    else:
        first = pricing
    bid = float(first.get("bid", 0) or 0)
    ask = float(first.get("ask", 0) or 0)
    if bid <= 0 or ask <= 0:
        return 0.0
    return (bid + ask) / 2.0


def compute_position_units(
    *,
    instrument: str,
    balance: float,
    account_currency: str,
    risk_pct: float,
    stop_pips: float,
    safety_cap_units: int | None,
    client,
) -> SizingBreakdown:
    """Compute the entry unit count for a fresh trade.

    Formula (matches backtest.py::_dsl_position_size in intent):
        risk_account   = balance × risk_pct
        pip_value_acct = pip_size × quote_to_account_rate
        desired_units  = risk_account / (stop_pips × pip_value_acct)
        capped_units   = min(desired_units, safety_cap_units) if cap else desired_units

    Runner treats the return as the ABSOLUTE unit count — sign is
    applied by the caller based on signal direction. Zero return
    (misconfigured spec, zero stop, zero risk) is legal and the
    caller must decide whether to skip the trade.
    """
    parts = instrument.upper().replace("/", "_").split("_")
    if len(parts) != 2:
        raise ValueError(f"unsupported instrument symbol: {instrument}")
    _, quote_currency = parts

    pip_size = _pip_size_for_pair(instrument)
    rate = _quote_to_account_rate(
        quote_currency=quote_currency,
        account_currency=account_currency,
        client=client,
    )
    pip_value_account = pip_size * rate

    if stop_pips <= 0 or pip_value_account <= 0:
        return SizingBreakdown(
            balance=balance,
            risk_pct=risk_pct,
            stop_pips=stop_pips,
            pip_value_account=pip_value_account,
            account_currency=account_currency,
            quote_to_account_rate=rate,
            desired_units=0,
            capped_units=0,
            safety_cap=safety_cap_units,
            notes="degenerate_inputs",
        )

    risk_account = balance * risk_pct
    desired_units = int(risk_account / (stop_pips * pip_value_account))
    if safety_cap_units is not None and desired_units > safety_cap_units:
        capped_units = safety_cap_units
        notes = f"capped_at_safety_limit_{safety_cap_units}"
    else:
        capped_units = desired_units
        notes = "ok"

    return SizingBreakdown(
        balance=balance,
        risk_pct=risk_pct,
        stop_pips=stop_pips,
        pip_value_account=pip_value_account,
        account_currency=account_currency,
        quote_to_account_rate=rate,
        desired_units=desired_units,
        capped_units=capped_units,
        safety_cap=safety_cap_units,
        notes=notes,
    )


__all__ = ["SizingBreakdown", "compute_position_units"]
