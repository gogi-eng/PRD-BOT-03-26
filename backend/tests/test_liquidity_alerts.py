"""Крупные заявки и ликвидации: сообщение есть, ордера нет, проверка входа против ликвидации."""
from pathlib import Path

from prd_agent.market.liquidity_alerts import (
    DEFAULT_MIN_WALL_BOOK_FRAC,
    MIN_WALL_USDT,
    AlertState,
    LiqPrint,
    collect_alerts,
    fade_entry_side,
    is_significant_wall,
    read_settings,
    simulate_fade,
    summarize_fade,
)

ROOT = Path(__file__).resolve().parents[2]


def _book():
    bids = [[80000, 0.01] for _ in range(9)]
    bids.append([80000, 1.0])
    asks = [[80100, 0.01] for _ in range(10)]
    return {"bids": bids, "asks": asks}


def test_settings_off_by_default():
    assert read_settings({}).enabled is False
    assert read_settings({"liquidity_alerts": {"enabled": True}}).enabled is True


def test_wall_floor_is_10000_usdt_and_two_percent_of_book():
    assert MIN_WALL_USDT == 10_000.0
    assert DEFAULT_MIN_WALL_BOOK_FRAC == 0.02
    defaults = read_settings({"liquidity_alerts": {"enabled": True}})
    assert defaults.min_wall_usd == 10_000.0
    assert defaults.min_wall_book_frac == 0.02
    clamped = read_settings({"liquidity_alerts": {"min_wall_usd": 500, "min_wall_book_frac": 0}})
    assert clamped.min_wall_usd == 10_000.0
    assert clamped.min_wall_book_frac == 0.01


def test_is_significant_wall_needs_notional_and_book_share():
    settings = read_settings({"liquidity_alerts": {"enabled": True}})
    assert is_significant_wall(10_000, 200_000, settings) is True
    assert is_significant_wall(9_999.99, 50_000, settings) is False
    assert is_significant_wall(20_000, 1_100_000, settings) is False
    assert is_significant_wall(10_000, 200_000.0, settings) is True
    assert is_significant_wall(None, 200_000, settings) is False
    assert is_significant_wall(20_000, 0, settings) is False
    assert is_significant_wall(float("nan"), 200_000, settings) is False
    assert is_significant_wall(20_000, float("inf"), settings) is False


def test_small_or_thin_walls_are_not_tracked():
    settings = read_settings({"liquidity_alerts": {"enabled": True, "wall_persist_sec": 45}})
    tiny_bid = {
        "bids": [[100.0, 5.0] for _ in range(9)] + [[100.0, 50.0]],
        "asks": [[101.0, 5.0] for _ in range(10)],
    }
    state = AlertState()
    first = collect_alerts({"SOLUSDT": tiny_bid}, [], state, now=1_000.0, settings=settings)
    later = collect_alerts({"SOLUSDT": tiny_bid}, [], state, now=1_060.0, settings=settings)
    assert first == []
    assert later == []
    assert state.wall_first_seen == {}

    thin_vs_book = {
        "bids": [[100.0, 200.0] for _ in range(150)] + [[100.0, 1_100.0]],
        "asks": [[101.0, 200.0] for _ in range(150)],
    }
    thin_state = AlertState()
    thin_first = collect_alerts(
        {"ETHUSDT": thin_vs_book}, [], thin_state, now=1_000.0, settings=settings
    )
    thin_later = collect_alerts(
        {"ETHUSDT": thin_vs_book}, [], thin_state, now=1_060.0, settings=settings
    )
    assert thin_first == []
    assert thin_later == []
    assert thin_state.wall_first_seen == {}


def test_large_wall_still_alerts_without_placing_order():
    settings = read_settings(
        {
            "liquidity_alerts": {
                "enabled": True,
                "min_wall_usd": 10_000,
                "min_wall_book_frac": 0.02,
                "wall_multiple": 5,
                "wall_persist_sec": 45,
            }
        }
    )
    state = AlertState()
    first = collect_alerts({"BTCUSDT": _book()}, [], state, now=1_000.0, settings=settings)
    assert first == []
    assert state.wall_first_seen
    second = collect_alerts({"BTCUSDT": _book()}, [], state, now=1_050.0, settings=settings)
    assert len(second) == 1
    assert second[0].kind == "wall"
    assert second[0].usd >= 10_000
    assert "не открываю" in second[0].text
    assert "place_order" not in second[0].text


def test_wall_module_does_not_place_orders_or_enable_search():
    text = (ROOT / "prd_agent" / "market" / "liquidity_alerts.py").read_text(encoding="utf-8")
    assert "place_order" not in text
    assert "trailingStop" not in text
    assert "channel_trade_search" not in text
    assert "create_order" not in text


def test_wall_alerts_only_after_it_stays_and_does_not_order():
    settings = read_settings(
        {
            "liquidity_alerts": {
                "enabled": True,
                "min_wall_usd": 25000,
                "wall_multiple": 5,
                "wall_persist_sec": 45,
            }
        }
    )
    state = AlertState()
    first = collect_alerts({"BTCUSDT": _book()}, [], state, now=1_000.0, settings=settings)
    assert first == []
    second = collect_alerts({"BTCUSDT": _book()}, [], state, now=1_050.0, settings=settings)
    assert len(second) == 1
    assert second[0].kind == "wall"
    assert "не открываю" in second[0].text
    assert "place_order" not in second[0].text


def test_liquidation_burst_names_longs():
    settings = read_settings(
        {"liquidity_alerts": {"enabled": True, "min_liq_usd": 20000, "liq_window_sec": 180}}
    )
    now = 1_700_000_000.0
    events = [
        {"symbol": "ETHUSDT", "side": "Buy", "price": 2500, "size": 10, "timestamp": int(now * 1000) - 10_000}
    ]
    alerts = collect_alerts({}, events, AlertState(), now=now, settings=settings)
    assert len(alerts) == 1
    assert alerts[0].side == "Buy"
    assert "лонги" in alerts[0].text
    assert alerts[0].usd == 25000


def test_fade_buys_after_long_liquidation_and_fees_can_erase_a_tiny_bounce():
    assert fade_entry_side("Buy") == "Buy"
    assert fade_entry_side("Sell") == "Sell"
    start = 1_700_000_000_000
    candles = [(start + i * 60_000, 100.0) for i in range(40)]
    # 10 минут спустя цена 100.20, ещё через 15 минут 100.40 — отскок после слива лонгов.
    candles[10] = (start + 10 * 60_000, 100.20)
    candles[25] = (start + 25 * 60_000, 100.40)
    event = LiqPrint(ts_ms=start, symbol="BTCUSDT", position_side="Buy", usd=50_000)
    trades = simulate_fade([event], {"BTCUSDT": candles}, delay_min=10, hold_min=15, min_usd=20_000)
    assert len(trades) == 1
    assert trades[0].entry_side == "Buy"
    assert trades[0].net_pct > 0

    flat = [(start + i * 60_000, 100.0) for i in range(40)]
    flat[10] = (start + 10 * 60_000, 100.0)
    flat[25] = (start + 25 * 60_000, 100.05)
    tiny = simulate_fade(
        [LiqPrint(ts_ms=start, symbol="BTCUSDT", position_side="Buy", usd=50_000)],
        {"BTCUSDT": flat},
        delay_min=10,
        hold_min=15,
        min_usd=20_000,
    )
    assert tiny[0].gross_pct > 0
    assert tiny[0].net_pct < 0
    summary = summarize_fade(tiny)
    assert summary["n"] == 1
    assert summary["wins"] == 0


def test_overlapping_liquidation_is_one_trade():
    start = 1_700_000_000_000
    candles = [(start + i * 60_000, 100.0 + i * 0.01) for i in range(50)]
    events = [
        LiqPrint(ts_ms=start, symbol="SOLUSDT", position_side="Sell", usd=30_000),
        LiqPrint(ts_ms=start + 60_000, symbol="SOLUSDT", position_side="Sell", usd=30_000),
    ]
    trades = simulate_fade(events, {"SOLUSDT": candles}, delay_min=10, hold_min=15, min_usd=20_000)
    assert len(trades) == 1
    assert trades[0].entry_side == "Sell"
