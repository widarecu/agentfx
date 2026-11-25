"""Notional leverage limits shared by backtest and paper sizing."""

DEFAULT_LEVERAGE = 30


def max_leveraged_units(
    balance: float, notional_per_unit: float, leverage: int = DEFAULT_LEVERAGE
) -> float:
    return balance * leverage / notional_per_unit
