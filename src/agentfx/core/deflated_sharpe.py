"""Full-window Deflated Sharpe Ratio (Bailey & López de Prado, 2014)."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from statistics import NormalDist

import numpy as np

DEFAULT_DSR_THRESHOLD = 0.95
DSR_WINDOW_OVERLAP = 0.50
_NORMAL = NormalDist()
_EULER_GAMMA = 0.5772156649015329
_ANNUALIZATION = sqrt(252)


@dataclass(frozen=True)
class SharpeSample:
    size: int
    sharpe: float
    skewness: float
    kurtosis: float

    @classmethod
    def from_returns(cls, daily_returns: np.ndarray) -> SharpeSample:
        if len(daily_returns) < 4 or not np.isfinite(daily_returns).all():
            raise ValueError("at least four finite daily returns are required")
        mean = float(np.mean(daily_returns))
        centered = daily_returns - mean
        deviation = float(np.std(daily_returns))
        if not isfinite(deviation) or deviation <= 0:
            raise ValueError("daily returns have no measurable variation")
        standardized = centered / deviation
        return cls(
            len(daily_returns),
            mean / deviation,
            float(np.mean(standardized**3)),
            float(np.mean(standardized**4)),
        )

    @property
    def annualized_sharpe(self) -> float:
        return self.sharpe * _ANNUALIZATION


def deflated_sharpe(
    sample: SharpeSample, *, trial_count: int, threshold: float
) -> tuple[float, float]:
    """Return (DSR probability, implied annualized Sharpe bar).

    Estimate cross-trial Sharpe variance under a zero-edge, independent
    daily-return null as 1/(T-1); historical trials lack full-window
    Sharpes for a consistent empirical variance estimate. The paper's
    expected-maximum correction uses this variance and the raw trial
    count; for one trial the null benchmark is zero. The candidate's
    sampling error uses its skewness and Pearson (non-excess) kurtosis.
    The displayed bar conditions on that estimated sampling error.
    """
    if trial_count < 1 or not 0 < threshold < 1:
        raise ValueError("DSR requires a positive trial count and threshold in (0, 1)")
    null_std = 1 / sqrt(sample.size - 1)
    benchmark = 0.0
    if trial_count > 1:
        # Use lower-tail probabilities to avoid rounding 1 - p to 1.
        benchmark = -null_std * (
            (1 - _EULER_GAMMA) * _NORMAL.inv_cdf(1 / trial_count)
            + _EULER_GAMMA * _NORMAL.inv_cdf(np.exp(-1) / trial_count)
        )
    variance_factor = (
        1
        - sample.skewness * sample.sharpe
        + (sample.kurtosis - 1) * sample.sharpe**2 / 4
    )
    if not isfinite(variance_factor) or variance_factor <= 0:
        raise ValueError("the Sharpe sampling variance is not positive and finite")
    error = null_std * sqrt(variance_factor)
    bar = (benchmark + _NORMAL.inv_cdf(threshold) * error) * _ANNUALIZATION
    return _NORMAL.cdf((sample.sharpe - benchmark) / error), bar
