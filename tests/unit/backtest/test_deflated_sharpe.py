import numpy as np
import pytest

from agentfx.core.deflated_sharpe import SharpeSample, deflated_sharpe


@pytest.mark.parametrize(
    ("trial_count", "probability", "bar"),
    [
        (1, 0.8757166085, 2.7155894397),
        (1000, 0.0231947756, 7.9089667778),
    ],
)
def test_deflated_sharpe_reference(trial_count, probability, bar):
    # T=100, daily Sharpe=.12, skew=-.5, Pearson kurtosis=4:
    # SE=sqrt((1+.5*.12+3*.12**2/4)/99).
    # At 1000 trials the two normal quantiles are 3.0902323062 and 3.3758953911.
    sample = SharpeSample(100, 0.12, -0.5, 4.0)
    result = deflated_sharpe(sample, trial_count=trial_count, threshold=0.95)
    assert result == pytest.approx((probability, bar), abs=1e-5)


def test_sample_uses_pearson_kurtosis():
    sample = SharpeSample.from_returns(np.array([0.0, 0.0, 0.0, 0.04]))
    assert sample.size == 4
    assert sample.sharpe == pytest.approx(1 / np.sqrt(3))
    assert sample.skewness == pytest.approx(2 / np.sqrt(3))
    assert sample.kurtosis == pytest.approx(7 / 3)


@pytest.mark.parametrize("daily_returns", [[1, 2, 3], [0, 0, 0, 0], [0, 0, 0, np.nan]])
def test_invalid_samples_are_rejected(daily_returns):
    with pytest.raises(ValueError):
        SharpeSample.from_returns(np.array(daily_returns))
