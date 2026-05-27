"""Тесты фильтра «небольшой профит» для copy mirror."""
from prd_agent.copy_mirror.filters import profit_in_band
from prd_agent.copy_mirror.position_math import unrealized_profit_pct


def test_profit_band_ok():
    ok, _ = profit_in_band(0.35, min_pct=0.12, max_pct=1.5)
    assert ok


def test_profit_too_low():
    ok, reason = profit_in_band(0.05, min_pct=0.12, max_pct=1.5)
    assert not ok
    assert "min" in reason


def test_profit_too_high():
    ok, reason = profit_in_band(2.0, min_pct=0.12, max_pct=1.5)
    assert not ok
    assert "поздно" in reason or "max" in reason


def test_unrealized_long():
    pct = unrealized_profit_pct("Buy", 100.0, 100.2)
    assert 0.19 < pct < 0.21
