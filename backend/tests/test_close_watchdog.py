"""Тесты CloseWatchdog: все сделки, алерт после >2 убытков / некорректных."""
from __future__ import annotations

import time
from pathlib import Path

from prd_agent.positions.close_watchdog import (
    CloseWatchdog,
    CloseWatchdogConfig,
    TrackedOpen,
)


def test_snapshot_tracks_manual_and_bot(tmp_path: Path):
    wd = CloseWatchdog(CloseWatchdogConfig(enabled=True), tmp_path)
    positions = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": 0.01, "avgPrice": 100},
        {"symbol": "ETHUSDT", "side": "Sell", "size": 1, "avgPrice": 2000},
    ]
    wd.snapshot_opens(positions, bot_symbols={"BTCUSDT"})
    assert wd.open_map["BTCUSDT|Buy"].origin == "bot"
    assert wd.open_map["ETHUSDT|Sell"].origin == "manual"


def test_snapshot_uses_exchange_created_time(tmp_path: Path):
    wd = CloseWatchdog(CloseWatchdogConfig(enabled=True), tmp_path)
    created = (time.time() - 3600) * 1000.0
    wd.snapshot_opens(
        [
            {
                "symbol": "BTCUSDT",
                "side": "Buy",
                "size": 0.01,
                "avgPrice": 100,
                "createdTime": str(int(created)),
            }
        ],
        bot_symbols=set(),
    )
    t = wd.open_map["BTCUSDT|Buy"]
    assert t.opened_reliable is True
    assert abs(t.opened_at_ms - created) < 2000.0


def test_alert_on_third_consecutive_loss(tmp_path: Path):
    wd = CloseWatchdog(
        CloseWatchdogConfig(
            enabled=True,
            alert_when_losses_gt=2,
            alert_when_bad_closes_gt=99,
            alert_cooldown_sec=0,
            fast_loss_minutes=0.01,
            min_loss_usdt_for_streak=0.01,
            min_loss_usdt_for_bad=0.01,
        ),
        tmp_path,
    )
    for i, oid in enumerate(("1", "2", "3")):
        wd.open_map["AAAUSDT|Buy"] = TrackedOpen(
            symbol="AAAUSDT",
            side="Buy",
            entry=100,
            qty=1,
            origin="manual",
            opened_at_ms=(time.time() - 3600) * 1000,
            opened_reliable=True,
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
            min_loss_usdt_for_bad=0.01,
            min_loss_usdt_for_streak=0.01,
        ),
        tmp_path,
    )
    for i, oid in enumerate(("a", "b", "c")):
        wd.open_map["BBBUSDT|Sell"] = TrackedOpen(
            symbol="BBBUSDT",
            side="Sell",
            entry=50,
            qty=1,
            origin="bot",
            opened_at_ms=(time.time() - 60) * 1000,  # 1 минута — fast loss
            opened_reliable=True,
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


def test_unreliable_age_zero_not_fast_loss(tmp_path: Path):
    """age≈0 от adopt/снимка — НЕ «быстрый убыток»."""
    wd = CloseWatchdog(
        CloseWatchdogConfig(
            enabled=True,
            alert_when_losses_gt=99,
            alert_when_bad_closes_gt=0,  # алерт на 1-м bad
            alert_cooldown_sec=0,
            fast_loss_minutes=5.0,
            treat_unreliable_age_as_fast=False,
            min_loss_usdt_for_bad=0.01,
        ),
        tmp_path,
    )
    wd.open_map["DUSTUSDT|Buy"] = TrackedOpen(
        symbol="DUSTUSDT",
        side="Buy",
        entry=100,
        qty=1,
        origin="manual",
        opened_at_ms=time.time() * 1000.0,  # только что «увидели»
        opened_reliable=False,
    )
    is_bad, reasons = wd.classify_close(
        pnl_usdt=-0.5,
        entry=100,
        exit_price=99.5,
        side="Buy",
        age_minutes=0.0,
        age_reliable=False,
    )
    assert is_bad is False
    assert reasons == []

    msg = wd.on_closed_trade(
        {
            "symbol": "DUSTUSDT",
            "side": "Buy",
            "closedPnl": -0.5,
            "avgEntryPrice": 100,
            "avgExitPrice": 99.5,
            "orderId": "u1",
        },
        origin="manual",
        order_id="u1",
    )
    # без bad streak и без loss streak порога — нет алерта
    assert msg is None
    assert wd.consecutive_bad == 0


def test_dust_loss_ignored_for_streak(tmp_path: Path):
    wd = CloseWatchdog(
        CloseWatchdogConfig(
            enabled=True,
            alert_when_losses_gt=2,
            alert_when_bad_closes_gt=99,
            alert_cooldown_sec=0,
            min_loss_usdt_for_streak=0.15,
            min_loss_usdt_for_bad=0.15,
        ),
        tmp_path,
    )
    for oid in ("d1", "d2", "d3"):
        wd.open_map["TINYUSDT|Buy"] = TrackedOpen(
            symbol="TINYUSDT",
            side="Buy",
            entry=10,
            qty=1,
            origin="bot",
            opened_at_ms=(time.time() - 3600) * 1000,
            opened_reliable=True,
        )
        msg = wd.on_closed_trade(
            {
                "symbol": "TINYUSDT",
                "side": "Buy",
                "closedPnl": -0.02,  # копейки
                "avgEntryPrice": 10,
                "avgExitPrice": 9.99,
                "orderId": oid,
            },
            origin="bot",
            order_id=oid,
        )
        assert msg is None
    assert wd.consecutive_losses == 0


def test_real_fast_loss_reliable_age_is_bad(tmp_path: Path):
    wd = CloseWatchdog(
        CloseWatchdogConfig(
            enabled=True,
            fast_loss_minutes=5.0,
            min_loss_usdt_for_bad=0.1,
            treat_unreliable_age_as_fast=False,
        ),
        tmp_path,
    )
    is_bad, reasons = wd.classify_close(
        pnl_usdt=-1.0,
        entry=100,
        exit_price=99,
        side="Buy",
        age_minutes=0.8,
        age_reliable=True,
    )
    assert is_bad is True
    assert any("быстрый убыток" in r for r in reasons)


def test_format_age_instant_label():
    txt = CloseWatchdog.format_age_text(0.0, age_reliable=False)
    assert "мгновенный учёт" in txt
    assert CloseWatchdog.format_age_text(12.5, age_reliable=True) == "12.5 мин"


def test_profit_resets_loss_streak(tmp_path: Path):
    wd = CloseWatchdog(
        CloseWatchdogConfig(
            enabled=True,
            alert_cooldown_sec=0,
            alert_when_losses_gt=2,
            min_loss_usdt_for_streak=0.01,
        ),
        tmp_path,
    )
    for oid, pnl in (("1", -1.0), ("2", -1.0), ("3", 2.0), ("4", -1.0)):
        wd.open_map["CCCUSDT|Buy"] = TrackedOpen(
            symbol="CCCUSDT",
            side="Buy",
            entry=10,
            qty=1,
            origin="bot",
            opened_at_ms=(time.time() - 3600) * 1000,
            opened_reliable=True,
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


def test_missing_open_time_not_fast_loss(tmp_path: Path):
    wd = CloseWatchdog(CloseWatchdogConfig(enabled=True, fast_loss_minutes=5.0), tmp_path)
    is_bad, reasons = wd.classify_close(
        pnl_usdt=-1.0,
        entry=100,
        exit_price=99.5,
        side="Buy",
        age_minutes=None,
        age_reliable=False,
    )
    assert is_bad is False
    assert reasons == []
