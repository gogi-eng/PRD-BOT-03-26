"""Тесты retest_watchlist: WAIT → CONFIRMED по окну свечей."""
from __future__ import annotations

from prd_agent.entry.retest_watchlist import RetestWatchlist


def _candle(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


def _make_impulse_retest_confirm_klines(side: str = "BUY", atr: float = 100.0) -> list:
    """Импульс вверх, ретест вниз, подтверждение вверх (3 свечи)."""
    if side == "BUY":
        impulse = _candle(1000, 1100, 995, 1090)
        retest = _candle(1090, 1095, 1050, 1060)
        confirm = _candle(1060, 1120, 1055, 1110)
    else:
        impulse = _candle(1100, 1105, 1000, 1010)
        retest = _candle(1010, 1050, 1005, 1040)
        confirm = _candle(1040, 1045, 990, 995)
    pad = [_candle(1000, 1005, 995, 1000) for _ in range(5)]
    return pad + [impulse, retest, confirm]


def test_register_and_confirm_via_window():
    cfg = {
        "zone_entry": {
            "require_impulse_retest": True,
            "retest_watchlist": {"enabled": True, "scan_candles": 12, "ttl_minutes": 60},
        },
        "entry": {"impulse_min_body_atr": 0.3, "retest_max_body_ratio": 0.5},
    }
    wl = RetestWatchlist(cfg)
    wl.register_breakout("BTCUSDT", "BUY", bos_level=1090.0)
    assert wl.get_phase("BTCUSDT", "BUY") == "WAIT_RETEST"

    klines = _make_impulse_retest_confirm_klines("BUY", atr=100.0)
    ok, reason = wl.evaluate("BTCUSDT", "BUY", klines, atr_value=100.0, confidence=0.75)
    assert ok is True
    assert "CONFIRMED" in reason
    assert wl.get_phase("BTCUSDT", "BUY") == "CONFIRMED"


def test_wait_when_no_registration_and_no_pattern():
    cfg = {
        "zone_entry": {"require_impulse_retest": True, "retest_watchlist": {"enabled": True}},
        "entry": {"impulse_min_body_atr": 0.45},
    }
    wl = RetestWatchlist(cfg)
    flat = [_candle(100, 101, 99, 100) for _ in range(8)]
    ok, reason = wl.evaluate("ETHUSDT", "BUY", flat, atr_value=10.0, confidence=0.7)
    assert ok is False
    assert "impulse_retest" in reason or "retest" in reason


def test_disabled_passes_through():
    cfg = {"zone_entry": {"retest_watchlist": {"enabled": False}}}
    wl = RetestWatchlist(cfg)
    ok, reason = wl.evaluate("BTCUSDT", "BUY", [], atr_value=0.0)
    assert ok is True
    assert reason == ""
