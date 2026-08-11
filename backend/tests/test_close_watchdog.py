"""Тесты CloseWatchdog: все сделки, алерт после >2 убытков / некорректных."""
from __future__ import annotations

import time
from pathlib import Path

from prd_agent.positions.close_watchdog import CloseWatchdog, CloseWatchdogConfig


def test_snapshot_tracks_manual_and_bot(tmp_path: Path):
    wd = CloseWatchdog(CloseWatchdogConfig(enabled=True), tmp_path)
    positions = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": 0.01, "avgPrice": 100},
        {"symbol": "ETHUSDT", "side": "Sell", "size": 1, "avgPrice": 2000},
    ]
    wd.snapshot_opens(positions, bot_symbols={"BTCUSDT"})
    assert wd.open_map["BTCUSDT|Buy"].origin == "bot"
    assert wd.open_map["ETHUSDT|Sell"].origin == "manual"


def test_alert_on_third_consecutive_loss(tmp_path: Path):
    wd = CloseWatchdog(
        CloseWatchdogConfig(
            enabled=True,
            alert_when_losses_gt=2,
            alert_when_bad_closes_gt=99,
            alert_cooldown_sec=0,
            fast_loss_minutes=0.01,  # не считаем bad из‑за возраста
        ),
        tmp_path,
    )
    # возраст большой — не bad, но убыток
    wd.open_map["AAAUSDT|Buy"] = wd.open_map.get("AAAUSDT|Buy")  # noqa — set below
    from prd_agent.positions.close_watchdog import TrackedOpen

    for i, oid in enumerate(("1", "2", "3")):
        wd.open_map["AAAUSDT|Buy"] = TrackedOpen(
            symbol="AAAUSDT",
            side="Buy",
            entry=100,
            qty=1,
            origin="manual",
            opened_at_ms=(time.time() - 3600) * 1000,
        )
        msg = wd.on_closed_trade(
            {
                "symbol": "AAAUSDT",
                "side": "Buy",
                "closedPnl": -1.0,
                "avgEntryPrice": 100,
                "avgExitPrice": 99,
                "orderId": oid,
            },
            origin="manual",
            order_id=oid,
        )
        if i < 2:
            assert msg is None
            assert wd.consecutive_losses == i + 1
        else:
            assert msg is not None
            assert "АВАРИЯ ЗАКРЫТИЙ" in msg
            assert wd.consecutive_losses == 3


def test_bad_fast_loss_streak_alerts(tmp_path: Path):
    wd = CloseWatchdog(
        CloseWatchdogConfig(
            enabled=True,
            alert_when_losses_gt=99,
            alert_when_bad_closes_gt=2,
            alert_cooldown_sec=0,
            fast_loss_minutes=5.0,
        ),
        tmp_path,
    )
    from prd_agent.positions.close_watchdog import TrackedOpen

    for i, oid in enumerate(("a", "b", "c")):
        wd.open_map["BBBUSDT|Sell"] = TrackedOpen(
            symbol="BBBUSDT",
            side="Sell",
            entry=50,
            qty=1,
            origin="bot",
            opened_at_ms=(time.time() - 60) * 1000,  # 1 минута — fast loss
        )
        msg = wd.on_closed_trade(
            {
                "symbol": "BBBUSDT",
                "side": "Sell",
                "closedPnl": -0.5,
                "avgEntryPrice": 50,
                "avgExitPrice": 51,
                "orderId": oid,
            },
            origin="bot",
            order_id=oid,
        )
        if i < 2:
            assert msg is None
        else:
            assert msg is not None
            assert "быстрый убыток" in msg or "Некорректных" in msg


def test_profit_resets_loss_streak(tmp_path: Path):
    wd = CloseWatchdog(
        CloseWatchdogConfig(enabled=True, alert_cooldown_sec=0, alert_when_losses_gt=2),
        tmp_path,
    )
    from prd_agent.positions.close_watchdog import TrackedOpen

    for oid, pnl in (("1", -1.0), ("2", -1.0), ("3", 2.0), ("4", -1.0)):
        wd.open_map["CCCUSDT|Buy"] = TrackedOpen(
            symbol="CCCUSDT",
            side="Buy",
            entry=10,
            qty=1,
            origin="bot",
            opened_at_ms=(time.time() - 3600) * 1000,
        )
        wd.on_closed_trade(
            {
                "symbol": "CCCUSDT",
                "side": "Buy",
                "closedPnl": pnl,
                "avgEntryPrice": 10,
                "avgExitPrice": 10.1 if pnl > 0 else 9.9,
                "orderId": oid,
            },
            origin="bot",
            order_id=oid,
        )
    assert wd.consecutive_losses == 1
