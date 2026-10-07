import pandas as pd
import pytest

from arty_trading.validation.broker_spreads import BrokerSpreadHypothesis, analyze


def test_invalid_quotes_and_demo_not_reference():
    frame = pd.DataFrame({
        "observed_utc": ["2026.10.06 12:00:00"] * 3,
        "bid": [3000, 3000, 3000], "ask": [3000.2, 2999, 3001],
        "point": [.01] * 3, "spread_usd_per_oz": [.2, -1, .5],
        "broker": ["Exness-Demo"] * 3, "account_mode": [0] * 3,
        "symbol": ["XAUUSDm"] * 3,
    })
    result = analyze(frame)
    assert result["invalid_rows"] == 2
    assert result["overall"]["median"] == pytest.approx(.2)
    assert not result["allowed_for_reference_cost_model"]


def test_independent_floor_stress_once_and_validation():
    model = BrokerSpreadHypothesis(.6, "Independent broker evidence pending approval")
    assert model.effective_spread(.2, 1.5) == pytest.approx(.9)
    assert model.effective_spread(.8, 2) == pytest.approx(1.6)
    with pytest.raises(ValueError):
        BrokerSpreadHypothesis(.6, "")
    with pytest.raises(ValueError):
        model.effective_spread(float("nan"))
