"""Тесты Пакета 1 (P0): безопасное плечо по SL, min_notional 5 USDT (110094), округление SL по price_step."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from prd_agent.exchange.order_prep import PreparedOrder, prepare_order, round_price
from prd_agent.positions.position_steward import PositionSteward
from prd_agent.risk.guard import RiskGuard


def test_max_safe_leverage_caps_high_leverage_on_wide_sl():
    # SL = 2.5% от цены входа -> безопасное плечо = int(0.65 / (0.025 + 0.005)) = 21x
    safe_lev = RiskGuard.max_safe_leverage_for_sl(100.0, 97.5)
    assert safe_lev == 21

    # calculate_position_size с плечом 50x должен ограничить max_notional по safe_lev (21x), а не 50x
    g = RiskGuard({"risk": {}, "trading": {}})
    qty_50x = g.calculate_position_size(
        balance=100.0,
        risk_pct=10.0,  # большой risk_pct, чтобы сработал кэп max_notional
        entry=100.0,
        stop_loss=97.5,
        leverage=50,
    )
    qty_21x = g.calculate_position_size(
        balance=100.0,
        risk_pct=10.0,
        entry=100.0,
        stop_loss=97.5,
        leverage=21,
    )
    assert qty_50x == pytest.approx(qty_21x)
    assert qty_50x * 100.0 <= 100.0 * 0.1 * 21 + 1e-6


def test_prepare_order_rejects_below_min_notional_5usdt():
    class _FakeClient:
        async def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
            return {
                "min_qty": 1.0,
                "qty_step": 1.0,
                "price_step": 0.0001,
                "min_notional": 5.0,
            }

    # qty=100 * price=0.03 = 3.0 USDT < 5.0 USDT -> отказ до вызова API Bybit (110094)
    res = asyncio.run(
        prepare_order(
            _FakeClient(),
            symbol="CHEAPUSDT",
            leverage=10,
            qty=100.0,
            stop_loss=0.029,
            take_profit=0.033,
            entry_price=0.03,
        )
    )
    assert isinstance(res, PreparedOrder)
    assert res.ok is False
    assert "110094" in res.reason
    qty, sl, tp, err = res
    assert qty == 0.0
    assert "5.00 USDT" in err


def test_prepare_order_allows_valid_notional():
    class _FakeClient:
        async def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
            return {
                "min_qty": 1.0,
                "qty_step": 1.0,
                "price_step": 0.0001,
                "min_notional": 5.0,
            }

    res = asyncio.run(
        prepare_order(
            _FakeClient(),
            symbol="CHEAPUSDT",
            leverage=10,
            qty=200.0,
            stop_loss=0.02904,
            take_profit=0.03306,
            entry_price=0.03,
        )
    )
    assert res.ok is True
    qty, sl, tp, err = res
    assert err == ""
    assert qty == 200.0
    assert sl == pytest.approx(0.0290)
    assert tp == pytest.approx(0.0330)


def test_round_price_safe_direction_by_side():
    # LONG (Buy): округляет вниз (floor), SHORT (Sell): округляет вверх (ceil)
    assert round_price(0.00809, 0.0001, side="Buy") == pytest.approx(0.0080)
    assert round_price(0.00801, 0.0001, side="Sell") == pytest.approx(0.0081)


def test_position_steward_skips_sub_tick_sl_updates(tmp_path):
    """На дешёвой монете сдвиг меньше price_step не вызывает холостой update_stop_loss."""
    calls: List[float] = []

    class _FakeClient:
        async def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
            return {"min_qty": 1.0, "qty_step": 1.0, "price_step": 0.0001}

        async def update_stop_loss(self, symbol: str, sl: float, position_idx: int = 0) -> Dict[str, Any]:
            calls.append(sl)
            return {"success": True}

    class _FakeExchange:
        def __init__(self) -> None:
            self._client = _FakeClient()

        async def get_klines(self, symbol: str, interval: str = "15", limit: int = 80):
            return [
                {"open": 0.0080, "high": 0.0081, "low": 0.0079, "close": 0.0080, "volume": 1000}
                for _ in range(25)
            ]

    cfg = {
        "_root": str(tmp_path),
        "positions": {
            "trailing_enabled": True,
            "adopt_manual": True,
            "trailing_activation_pct": 1.0,
            "trailing_distance_pct": 1.0,
            "trailing_distance_atr_mult": 0.5,
            "breakeven_after_pct": 0.5,
            "sl_tp_guard": {"enabled": False},
            "manual_sl_guard": {"enabled": False},
            "exit_management": {"enabled": False},
            "tp_progress_exit": {"enabled": False},
            "adaptive_trailing": {"enabled": False},
            "trailing_volatility_regime": {"enabled": False},
            "garch_tp_peak_retrace": {"enabled": False},
            "manual_trailing_garch": {"enabled": False},
        },
    }
    steward = PositionSteward(cfg)
    ex = _FakeExchange()

    # Первый проход: подхват позиции и установка первого трейлинг-SL
    pos_row = {
        "symbol": "LOWCOINUSDT",
        "side": "Buy",
        "size": "1000",
        "avgPrice": "0.0080",
        "markPrice": "0.0083",
        "stopLoss": "0.0078",
        "takeProfit": "0.0090",
        "liqPrice": "0.0070",
    }
    asyncio.run(steward.manage(ex, [pos_row]))
    assert len(calls) == 1
    first_sl = calls[0]

    # Второй проход: цена сдвинулась на 0.000004 (+0.05% > 0.03%, но меньше 1 тика price_step=0.0001)
    pos_row["markPrice"] = "0.008304"
    pos_row["stopLoss"] = str(first_sl)
    asyncio.run(steward.manage(ex, [pos_row]))
    assert len(calls) == 1, "Холостой вызов update_stop_loss должен быть пропущен по price_step"
