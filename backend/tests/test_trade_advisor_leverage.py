"""Плечо через советника супервизора."""
from prd_agent.signals.types import UnifiedSignal
from prd_agent.supervisor.trade_advisor import TradeAdvisor
from prd_agent.supervisor.trade_supervisor import TradeSupervisor


def _cfg():
    return {
        "trading": {
            "leverage": 20,
            "min_signal_confidence": 0.68,
            "dynamic_leverage": {
                "enabled": True,
                "min": 20,
                "max": 50,
                "min_confidence": 0.68,
                "max_confidence": 0.95,
            },
        },
        "trade_supervisor": {
            "enabled": True,
            "leverage_advisor": {"enabled": True, "min_rr": 1.5},
        },
    }


def test_advisor_low_confidence_low_leverage():
    adv = TradeAdvisor(_cfg())
    sig = UnifiedSignal("BTCUSDT", "Buy", 0.70, "ta", entry=100, stop_loss=99, take_profit=103)
    advice = adv.recommend_leverage(sig, entry=100, stop_loss=99, take_profit=103)
    assert 20 <= advice.leverage <= 35


def test_advisor_high_confidence_high_leverage():
    adv = TradeAdvisor(_cfg())
    sig = UnifiedSignal(
        "ETHUSDT",
        "Buy",
        0.96,
        "ta",
        entry=100,
        stop_loss=98,
        take_profit=106,
        raw={"entry_zone": "demand", "regime": "trend", "htf_4h_trend": 1},
    )
    advice = adv.recommend_leverage(sig, entry=100, stop_loss=98, take_profit=106)
    assert advice.leverage >= 40


def test_supervisor_recommend_leverage_delegates():
    from pathlib import Path
    import tempfile

    from prd_agent.evolution.self_improver import SelfImprover

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg()
        cfg["_root"] = str(root)
        imp = SelfImprover(cfg, root)
        sup = TradeSupervisor(cfg, root / "sup", imp)
        sig = UnifiedSignal("SOLUSDT", "Sell", 0.92, "tg", entry=50, stop_loss=51, take_profit=47)
        advice = sup.recommend_leverage(sig, entry=50, stop_loss=51, take_profit=47)
        assert 20 <= advice.leverage <= 50
        assert "advisor score" in advice.reason
