"""Динамическое плечо по confidence."""
from prd_agent.risk.dynamic_leverage import (
    load_dynamic_leverage_settings,
    resolve_trade_leverage,
)


def test_resolve_at_min_confidence():
    cfg = {
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
        }
    }
    s = load_dynamic_leverage_settings(cfg)
    assert resolve_trade_leverage(0.68, s) == 20
    assert resolve_trade_leverage(0.50, s) == 20


def test_resolve_at_max_confidence():
    cfg = {
        "trading": {
            "dynamic_leverage": {
                "enabled": True,
                "min": 20,
                "max": 50,
                "min_confidence": 0.68,
                "max_confidence": 0.95,
            },
        }
    }
    s = load_dynamic_leverage_settings(cfg)
    assert resolve_trade_leverage(0.95, s) == 50
    assert resolve_trade_leverage(1.0, s) == 50


def test_resolve_mid_confidence_interpolates():
    cfg = {
        "trading": {
            "dynamic_leverage": {
                "enabled": True,
                "min": 20,
                "max": 50,
                "min_confidence": 0.68,
                "max_confidence": 0.95,
            },
        }
    }
    s = load_dynamic_leverage_settings(cfg)
    mid = resolve_trade_leverage(0.815, s)
    assert 30 <= mid <= 40


def test_disabled_uses_fallback():
    cfg = {"trading": {"leverage": 7, "dynamic_leverage": {"enabled": False}}}
    s = load_dynamic_leverage_settings(cfg)
    assert resolve_trade_leverage(0.99, s) == 7
