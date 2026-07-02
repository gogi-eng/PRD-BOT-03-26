"""Тест динамического плеча SPIKE SCANNER."""
from __future__ import annotations

from unittest.mock import MagicMock

from scripts.telegram_signal_agent import TelegramSignalAgent


def _minimal_cfg():
    return {
        "timezone_offset": 3,
        "trading": {
            "leverage": 20,
            "dynamic_leverage": {
                "enabled": True,
                "min": 20,
                "max": 50,
                "min_confidence": 0.68,
                "max_confidence": 0.95,
            },
        },
        "market_scanner": {
            "spike_scalp": {
                "enabled": True,
                "use_dynamic_leverage": True,
            },
        },
        "telegram_signal_agent": {},
    }


def test_spike_scanner_leverage_by_score(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "config.yaml").write_text("trading:\n  leverage: 20\n", encoding="utf-8")
    agent = TelegramSignalAgent.__new__(TelegramSignalAgent)
    agent.cfg = _minimal_cfg()
    agent.agent_cfg = {}
    agent._spike_scalp_cfg = MagicMock(use_dynamic_leverage=True)
    from prd_agent.risk.dynamic_leverage import load_dynamic_leverage_settings

    agent._dynamic_leverage_settings = load_dynamic_leverage_settings(agent.cfg)
    agent.default_leverage = 20
    agent.max_leverage = 50

    lev_low = TelegramSignalAgent._spike_scanner_leverage(agent, 72)
    lev_high = TelegramSignalAgent._spike_scanner_leverage(agent, 100)
    assert 20 <= lev_low <= 50
    assert lev_high == 50
    assert lev_high >= lev_low
