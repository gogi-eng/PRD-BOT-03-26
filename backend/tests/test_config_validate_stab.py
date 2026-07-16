"""Стабилизация: расширенная проверка config.yaml."""
from __future__ import annotations

from prd_agent.config_validate import validate_config_data


def _minimal_cfg(**overrides):
    base = {
        "bybit": {"api_key": "", "api_secret": "", "testnet": False, "category": "linear"},
        "telegram": {"bot_token": "", "chat_id": "", "control_polling_enabled": True},
        "trading": {
            "leverage": 20,
            "loop_interval_sec": 60,
            "max_positions": 6,
            "risk_pct_per_trade": 0.35,
            "min_signal_confidence": 0.85,
        },
        "risk": {
            "max_daily_loss_pct": 5.0,
            "max_consecutive_losses": 4,
            "max_daily_loss_usdt": 50,
            "max_trades_per_day": 12,
        },
        "quality_gate": {"min_rr_ratio": 2.0, "min_confidence": 0.85},
    }
    base.update(overrides)
    return base


def test_rejects_trading_typo_max_position():
    cfg = _minimal_cfg()
    cfg["trading"]["max_position"] = 5
    ok, errors = validate_config_data(cfg)
    assert not ok
    assert any("max_positions" in e for e in errors)


def test_rejects_bybit_typo_apiKey():
    cfg = _minimal_cfg()
    cfg["bybit"]["apiKey"] = "x"
    ok, errors = validate_config_data(cfg)
    assert not ok
    assert any("api_key" in e for e in errors)


def test_requires_bybit_and_telegram_sections():
    ok, errors = validate_config_data({"trading": {"leverage": 10, "max_positions": 3}, "risk": {}})
    assert not ok
    assert any("bybit" in e for e in errors)
    assert any("telegram" in e for e in errors)


def test_supervisor_v4_panic_minutes_range():
    cfg = _minimal_cfg()
    cfg["supervisor_v4"] = {"panic_minutes": 2}
    ok, errors = validate_config_data(cfg)
    assert not ok
    assert any("panic_minutes" in e for e in errors)


def test_accepts_bybit_read_keys_and_monitor_section():
    cfg = _minimal_cfg()
    cfg["bybit"]["read_api_key"] = "read"
    cfg["bybit"]["read_api_secret"] = "read_secret"
    cfg["bybit_monitor"] = {
        "enabled": True,
        "interval_sec": 300,
        "notify_telegram": False,
        "llm_summary": True,
        "kline_interval": "15",
        "kline_limit": 96,
        "include_funding": True,
        "include_oi": True,
        "include_liquidations": True,
        "alert_upnl_change_usdt": 15.0,
        "max_symbols": 8,
    }
    ok, errors = validate_config_data(cfg)
    assert ok, errors
