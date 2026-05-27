"""Проверка логики mismatch плеча."""
from prd_agent.exchange.leverage_apply import LeverageApplyResult


def test_mismatch_flag():
    r = LeverageApplyResult(requested=43, target=43, applied=3, max_instrument=100, ok=True)
    assert r.mismatch is True

def test_no_mismatch():
    r = LeverageApplyResult(requested=20, target=20, applied=20, max_instrument=50, ok=True)
    assert r.mismatch is False
