"""AW lab tuning 18.09.26: max_positions без manual, runtime scanner sync."""
from __future__ import annotations

from unittest.mock import MagicMock

from prd_agent.engine.orchestrator import UnifiedOrchestrator


def test_count_open_excludes_manual_positions():
    orch = object.__new__(UnifiedOrchestrator)
    orch.cfg = {"trading": {"count_manual_toward_max_positions": False}}
    orch.position_steward = MagicMock()
    orch.position_steward._bot_symbols = set()
    manual = MagicMock(origin="manual")
    orch.position_steward._tracked = {"ARBUSDT": manual}
    positions = [{"symbol": "ARBUSDT"}, {"symbol": "ETHUSDT"}]
    assert orch._count_open_for_max_positions(positions) == 0

    orch.position_steward._bot_symbols = {"ETHUSDT"}
    assert orch._count_open_for_max_positions(positions) == 1


def test_count_open_includes_all_when_default():
    orch = object.__new__(UnifiedOrchestrator)
    orch.cfg = {"trading": {}}
    orch.position_steward = MagicMock()
    positions = [{"symbol": "A"}, {"symbol": "B"}]
    assert orch._count_open_for_max_positions(positions) == 2


def test_runtime_controls_sync_market_scanner_from_yaml():
    from scripts.telegram_signal_agent import TelegramSignalAgent

    agent = object.__new__(TelegramSignalAgent)
    agent.agent_cfg = {
        "runtime_controls_sync_yaml": False,
        "runtime_controls_sync_market_scanner": True,
        "market_scanner_auto_execute": True,
        "auto_execute": False,
    }
    agent.auto_execute = False
    agent.market_scanner_auto_execute_default = True
    agent.state = {"agent_runtime_controls": {"market_scanner_auto_execute": False}}
    agent._ensure_runtime_controls_defaults()
    assert agent.state["agent_runtime_controls"]["market_scanner_auto_execute"] is True
