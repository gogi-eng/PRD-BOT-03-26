from __future__ import annotations

from fund_lite import DynamicRisk, HybridVotingPro, PortfolioRisk, RLMetaControllerPro


def test_hybrid_voting_pro():
    h = HybridVotingPro()
    assert h.combine(0.8, 0.8, 0.5) == "LONG"
    assert h.combine(0.2, 0.2, 0.3) == "SHORT"
    assert h.combine(0.5, 0.5, 0.5) == "HOLD"


def test_dynamic_risk():
    d = DynamicRisk()
    s = d.calculate({"volatility": 0.02, "ai_confidence": 0.8})
    assert 0 < s <= d.max_size


def test_portfolio_risk():
    p = PortfolioRisk(max_risk=0.2)
    p.set_exposure_fn(lambda: 0.15)
    assert p.check(0.04) is True
    assert p.check(0.06) is False


def test_rl_meta_pro_decide():
    rl = RLMetaControllerPro()
    out = rl.decide(0.5, {"ai_confidence": 55.0, "volatility": 0.02, "last_pnl": 0.0, "meta_drawdown": 0.0})
    assert "trade" in out and "risk_multiplier" in out
