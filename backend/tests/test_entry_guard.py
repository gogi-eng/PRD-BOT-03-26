"""Entry guard: price drift и limit для Telegram."""
from prd_agent.risk.entry_guard import EntryGuard
from prd_agent.signals.types import UnifiedSignal


def _cfg():
    return {
        "entry_guard": {
            "enabled": True,
            "max_market_drift_pct": 0.004,
            "max_limit_drift_pct": 0.008,
            "max_skip_drift_pct": 0.012,
            "telegram_limit_entry": True,
        }
    }


def test_market_when_small_drift():
    g = EntryGuard(_cfg())
    sig = UnifiedSignal("BTCUSDT", "Buy", 0.9, "ta_volatility", entry=100, stop_loss=98, take_profit=104)
    plan = g.plan_execution(sig, plan_entry=100.0, market_price=100.2)
    assert plan.allowed and plan.order_type == "Market"


def test_limit_for_telegram_moderate_drift():
    g = EntryGuard(_cfg())
    sig = UnifiedSignal(
        "ETHUSDT",
        "Buy",
        0.92,
        "telegram_inbox",
        entry=100,
        stop_loss=98,
        take_profit=106,
    )
    plan = g.plan_execution(sig, plan_entry=100.0, market_price=100.6)
    assert plan.allowed and plan.order_type == "Limit"
    assert plan.limit_price == 100.0


def test_skip_when_drift_too_large():
    g = EntryGuard(_cfg())
    sig = UnifiedSignal("SOLUSDT", "Sell", 0.88, "ta_volatility", entry=50, stop_loss=51, take_profit=47)
    plan = g.plan_execution(sig, plan_entry=50.0, market_price=51.0)
    assert not plan.allowed
    assert "entry_guard" in plan.reason
