"""Открытая позиция: обратный сигнал закрывает на бирже."""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from prd_agent.positions.scanner_reversal_sl import handle_scanner_reversal
from prd_agent.signals.side_utils import normalize_trade_side, trade_sides_opposite
from prd_agent.signals.types import UnifiedSignal


def test_trade_sides_opposite_buy_sell():
    assert trade_sides_opposite("Buy", "Sell")
    assert trade_sides_opposite("SELL", "BUY")
    assert not trade_sides_opposite("Buy", "Long")
    assert normalize_trade_side("Buy") == "BUY"


@dataclass
class _Setup:
    symbol: str
    scenario: str
    score: int
    invalidation: float = 0.0
    confirmed_bos: bool = False


@pytest.mark.asyncio
async def test_scanner_reversal_closes_on_opposite_signal():
    client = AsyncMock()
    client.close_position = AsyncMock(return_value={"success": True, "orderId": "x"})
    setup = _Setup(symbol="BTCUSDT", scenario="DUMP", score=80, confirmed_bos=False)
    position = {"side": "Buy", "size": 0.01, "avgPrice": 100.0, "markPrice": 99.0}
    cfg = {
        "enabled": True,
        "close_on_reversal": True,
        "tighten_sl": False,
        "min_score": 72,
        "require_confirmed_bos": False,
        "alert_telegram": False,
        "symbol_cooldown_sec": 0,
    }
    res = await handle_scanner_reversal(
        setup=setup,
        position=position,
        client=client,
        cfg=cfg,
        cooldown_state={},
    )
    assert res.closed is True
    client.close_position.assert_awaited_once()


@pytest.mark.asyncio
async def test_orchestrator_close_on_reverse_signal():
    from prd_agent.engine.orchestrator import UnifiedOrchestrator

    orch = MagicMock(spec=UnifiedOrchestrator)
    orch._reverse_signal_close = True
    orch._position_size = UnifiedOrchestrator._position_size
    orch.exchange = AsyncMock()
    orch.exchange.close_position = AsyncMock(
        return_value={"success": True, "orderId": "oid-1"}
    )
    orch.notifier = AsyncMock()
    orch.position_steward = MagicMock()
    orch.position_steward._tracked = {"CBRSUSDT": object()}
    orch.position_steward._bot_symbols = {"CBRSUSDT"}

    sig = UnifiedSignal(
        symbol="CBRSUSDT",
        side="SELL",
        confidence=0.7,
        source="own_multi_agent",
        entry=1.0,
        stop_loss=1.1,
        take_profit=0.9,
    )
    pos_row = {"side": "Buy", "size": 100.0, "positionIdx": 0}

    await UnifiedOrchestrator._close_on_reverse_signal(orch, sig, pos_row)

    orch.exchange.close_position.assert_awaited_once()
    orch.notifier.send.assert_awaited()
    assert "CBRSUSDT" not in orch.position_steward._bot_symbols


@pytest.mark.asyncio
async def test_orchestrator_same_side_does_not_close():
    from prd_agent.engine.orchestrator import UnifiedOrchestrator

    orch = MagicMock(spec=UnifiedOrchestrator)
    orch._reverse_signal_close = True
    orch._close_on_reverse_signal = AsyncMock()
    sig = UnifiedSignal(
        symbol="CBRSUSDT",
        side="BUY",
        confidence=0.6,
        source="own_multi_agent",
        entry=1.0,
        stop_loss=0.9,
        take_profit=1.2,
    )
    pos_row = {"side": "Buy", "size": 10.0}
    await UnifiedOrchestrator._handle_signal_with_open_position(orch, sig, pos_row)
    orch._close_on_reverse_signal.assert_not_awaited()


def test_apply_open_position_policy_reads_opposite_signal_exit():
    from prd_agent.engine.orchestrator import UnifiedOrchestrator

    orch = MagicMock()
    orch.cfg = {
        "positions": {
            "reverse_signal_close_enabled": False,
            "opposite_signal_exit": {"enabled": True},
        }
    }
    UnifiedOrchestrator._apply_open_position_policy(orch)
    assert orch._reverse_signal_close is True
