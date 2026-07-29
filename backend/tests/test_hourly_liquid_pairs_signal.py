#!/usr/bin/env python3
"""Unit-тесты лайт-логики сигнала hourly_liquid_pairs_report (без сети)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "hourly_liquid_pairs_report.py"


def _load_module():
    name = "hourly_liquid_pairs_report"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _pair(mod, **kwargs):
    defaults = dict(
        symbol="BTCUSDT",
        last_price=100.0,
        change_24h_pct=-2.0,
        turnover_24h=1_000_000_000.0,
        trend_1h="медвежий",
        trend_4h="медвежий",
        rsi_1h=40.0,
        htf_align="совпадает ↓",
        note="",
    )
    defaults.update(kwargs)
    return mod.PairAnalysis(**defaults)


def test_signal_short_when_htf_down_rsi_ok(mod):
    pairs = [
        _pair(mod, symbol="BTCUSDT", rsi_1h=42.0, change_24h_pct=-2.5),
        _pair(
            mod,
            symbol="ETHUSDT",
            last_price=2000.0,
            rsi_1h=30.0,  # ближе к зоне перепроданности — хуже score
            change_24h_pct=-3.0,
            turnover_24h=500_000_000.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is True
    assert d.side == "SHORT"
    assert d.symbol == "BTCUSDT"
    assert d.entry > 0 and d.sl > d.entry and d.tp < d.entry
    assert "совпадают" in d.reason.lower() or "вниз" in d.reason.lower()
    assert d.md_heading == "## Сигнал"


def test_signal_long_when_htf_up_rsi_ok(mod):
    pairs = [
        _pair(
            mod,
            symbol="BANKUSDT",
            last_price=0.36,
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=55.0,
            change_24h_pct=6.0,
            turnover_24h=200_000_000.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is True
    assert d.side == "LONG"
    assert d.symbol == "BANKUSDT"
    assert d.sl < d.entry < d.tp


def test_no_signal_rsi_overbought_long(mod):
    pairs = [
        _pair(
            mod,
            symbol="PUMPUSDT",
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=82.0,
            change_24h_pct=5.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is False
    assert "перекуплен" in d.reason.lower() or "rsi" in d.reason.lower()
    assert d.md_heading == "## Почему без сигнала"


def test_no_signal_rsi_oversold_short_chase(mod):
    pairs = [
        _pair(
            mod,
            symbol="DUMPUSDT",
            htf_align="совпадает ↓",
            rsi_1h=18.0,
            change_24h_pct=-8.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is False
    assert "перепродан" in d.reason.lower() or "rsi" in d.reason.lower()


def test_no_signal_pump_extremum(mod):
    pairs = [
        _pair(
            mod,
            symbol="COTIUSDT",
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=55.0,
            change_24h_pct=48.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is False
    assert "памп" in d.reason.lower() or "дамп" in d.reason.lower() or "экстрем" in d.reason.lower()


def test_no_signal_no_htf_align(mod):
    pairs = [
        _pair(
            mod,
            symbol="MIXUSDT",
            trend_1h="бычий",
            trend_4h="медвежий",
            htf_align="конфликт",
            rsi_1h=50.0,
            change_24h_pct=1.0,
        ),
        _pair(
            mod,
            symbol="SIDEUSDT",
            trend_1h="боковик",
            trend_4h="бычий",
            htf_align="смешанный",
            rsi_1h=50.0,
            change_24h_pct=-1.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is False
    assert "совпаден" in d.reason.lower() or "конфликт" in d.reason.lower() or "боковик" in d.reason.lower()


def test_picks_best_among_valid(mod):
    """Памп с HTF отбрасывается; выбирается умеренная пара."""
    pairs = [
        _pair(
            mod,
            symbol="COTIUSDT",
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=55.0,
            change_24h_pct=48.0,
            turnover_24h=90_000_000.0,
        ),
        _pair(
            mod,
            symbol="BANKUSDT",
            last_price=0.36,
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=55.0,
            change_24h_pct=6.0,
            turnover_24h=200_000_000.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is True
    assert d.symbol == "BANKUSDT"


def test_empty_pairs(mod):
    d = mod.decide_liquid_pairs_signal([])
    assert d.has_signal is False
    assert "нет данных" in d.reason.lower()


def test_telegram_format_with_and_without_signal(mod):
    report = {
        "generated_at_local": "2026-07-28T16:00:00+03:00",
        "timezone_offset_hours": 3,
        "liquid_pairs_total": 79,
        "analyzed_pairs": 15,
    }
    with_sig = mod.SignalDecision(
        has_signal=True,
        symbol="BTCUSDT",
        side="SHORT",
        entry=63500.0,
        sl=64452.5,
        tp=61595.0,
        reason="Тренды вниз совпадают.",
    )
    text_ok = mod.format_telegram_message(report, with_sig)
    assert "Условный совет" in text_ok
    assert "НЕ ордер" in text_ok or "НЕ автоторговля" in text_ok
    assert "BTCUSDT SHORT" in text_ok
    assert "Вход:" in text_ok

    no_sig = mod.SignalDecision(
        has_signal=False,
        reason="Сейчас без сигнала: RSI в крайней зоне.",
    )
    text_no = mod.format_telegram_message(report, no_sig)
    assert "Без сигнала" in text_no
    assert "RSI" in text_no


def test_accepts_dict_pairs(mod):
    raw = [
        {
            "symbol": "SOLUSDT",
            "last_price": 70.0,
            "change_24h_pct": -4.0,
            "turnover_24h": 400_000_000.0,
            "trend_1h": "медвежий",
            "trend_4h": "медвежий",
            "rsi_1h": 36.0,
            "htf_align": "совпадает ↓",
            "note": "",
        }
    ]
    d = mod.decide_liquid_pairs_signal(raw)
    assert d.has_signal is True
    assert d.symbol == "SOLUSDT"
    assert d.side == "SHORT"


def test_eul_like_mixed_htf_no_long(mod):
    """EUL-like: волатильный альт, конфликт HTF → LONG не выдаём."""
    pairs = [
        _pair(
            mod,
            symbol="BTCUSDT",
            last_price=64000.0,
            trend_1h="бычий",
            trend_4h="медвежий",
            htf_align="конфликт",
            rsi_1h=52.0,
            change_24h_pct=1.0,
            turnover_24h=3_000_000_000.0,
        ),
        _pair(
            mod,
            symbol="EULUSDT",
            last_price=1.8,
            trend_1h="бычий",
            trend_4h="медвежий",
            htf_align="конфликт",
            rsi_1h=48.0,
            change_24h_pct=5.0,
            turnover_24h=90_000_000.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is False
    assert d.side != "LONG"


def test_eul_like_htf_up_but_btc_bearish_blocks_alt_long(mod):
    """Альт с HTF↑, но BTC 4h↓ → LONG по альту блокируется."""
    pairs = [
        _pair(
            mod,
            symbol="BTCUSDT",
            last_price=64000.0,
            trend_1h="медвежий",
            trend_4h="медвежий",
            htf_align="совпадает ↓",
            rsi_1h=42.0,
            change_24h_pct=-2.0,
            turnover_24h=3_000_000_000.0,
        ),
        _pair(
            mod,
            symbol="EULUSDT",
            last_price=1.8,
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=55.0,
            change_24h_pct=4.0,
            turnover_24h=90_000_000.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    # SHORT по BTC допустим; LONG по EUL — нет
    assert d.has_signal is True
    assert d.symbol == "BTCUSDT"
    assert d.side == "SHORT"
    assert any("EULUSDT" in n for n in d.reject_notes)


def test_no_long_on_dump_bounce(mod):
    """LONG после суточного дампа (отскок) запрещён."""
    pairs = [
        _pair(
            mod,
            symbol="EULUSDT",
            last_price=1.7,
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=50.0,
            change_24h_pct=-9.0,
            turnover_24h=100_000_000.0,
        ),
        _pair(
            mod,
            symbol="BTCUSDT",
            last_price=65000.0,
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=55.0,
            change_24h_pct=1.5,
            turnover_24h=3_000_000_000.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is True
    assert d.symbol == "BTCUSDT"
    assert d.side == "LONG"
    assert any("отскок" in n.lower() or "дамп" in n.lower() for n in d.reject_notes)


def test_alt_extreme_tighter_than_major(mod):
    """Альт +9% режется (порог 8%), major +9% ещё кандидат."""
    pairs = [
        _pair(
            mod,
            symbol="EULUSDT",
            last_price=1.8,
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=55.0,
            change_24h_pct=9.0,
            turnover_24h=100_000_000.0,
        ),
        _pair(
            mod,
            symbol="ETHUSDT",
            last_price=1900.0,
            trend_1h="бычий",
            trend_4h="бычий",
            htf_align="совпадает ↑",
            rsi_1h=55.0,
            change_24h_pct=9.0,
            turnover_24h=2_000_000_000.0,
        ),
    ]
    d = mod.decide_liquid_pairs_signal(pairs)
    assert d.has_signal is True
    assert d.symbol == "ETHUSDT"
    assert d.side == "LONG"
