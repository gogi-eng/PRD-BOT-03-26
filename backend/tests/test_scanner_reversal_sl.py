"""Тесты scanner_reversal_sl: MarketSetup dataclass без .get()."""
from __future__ import annotations

from dataclasses import dataclass

from prd_agent.positions.scanner_reversal_sl import (
    build_reversal_alert_text,
    passes_reversal_filters,
)


@dataclass
class _FakeMarketSetup:
    symbol: str
    scenario: str
    score: int
    invalidation: float = 0.0
    confirmed_bos: bool = False


def test_passes_reversal_filters_dataclass_confirmed_bos_false_no_crash():
    """Раньше: confirmed_bos=False → setup.get() → AttributeError."""
    setup = _FakeMarketSetup(
        symbol="ETHUSDT",
        scenario="PUMP",
        score=80,
        confirmed_bos=False,
    )
    position = {"side": "Sell", "entry": 1700.0, "mark": 1740.0, "sl": 1680.0}
    cfg = {
        "enabled": True,
        "min_score": 72,
        "require_confirmed_bos": True,
        "close_on_reversal": False,
    }
    ok, reason = passes_reversal_filters(setup, position, cfg)
    assert ok is False
    assert reason == "no_bos"


def test_passes_reversal_filters_dict_still_works():
    setup = {
        "symbol": "ZECUSDT",
        "scenario": "DUMP",
        "score": 75,
        "confirmed_bos": True,
    }
    position = {"side": "Buy", "entry": 450.0}
    cfg = {"enabled": True, "min_score": 72, "require_confirmed_bos": True}
    ok, reason = passes_reversal_filters(setup, position, cfg)
    assert ok is True
    assert reason == "ok"


def test_build_reversal_alert_text_dataclass():
    setup = _FakeMarketSetup(symbol="VVVUSDT", scenario="DUMP", score=70)
    text = build_reversal_alert_text(setup, {"side": "Buy", "entry": 10.0, "mark": 9.5})
    assert "VVVUSDT" in text
    assert "DUMP" in text or "⬇" in text
