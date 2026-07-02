#!/usr/bin/env python3
"""Тесты детектора скальп-сигналов в журналах."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.analysis.scalp_signals import (
    analyze_scalp_signals,
    format_scalp_report_md,
    is_scalp_row,
    spike_scalp_config_notes,
)


def test_is_scalp_row_spike_scanner_source():
    ok, matched = is_scalp_row({"source": "SPIKE_SCANNER", "reason": "spike_scalp_pump"})
    assert ok is True
    assert "source=" in matched


def test_is_scalp_row_spike_reason():
    ok, matched = is_scalp_row({"source": "telegram", "reason": "spike_scalp_dump"})
    assert ok is True
    assert matched.startswith("reason=")


def test_is_scalp_row_scanner_kind_in_raw():
    ok, _ = is_scalp_row(
        {
            "source": "MARKET_SCANNER",
            "raw": {"scanner_kind": "spike_scalp", "raw_text": "pump_dump_spike BTCUSDT"},
        }
    )
    assert ok is True


def test_is_scalp_row_legacy_scalp_session():
    ok, _ = is_scalp_row(
        {
            "event": "closed",
            "entry_context": {"strategy": "scalp_session", "entry_zone": "scalp_session"},
        }
    )
    assert ok is True


def test_is_scalp_row_negative_regular_telegram():
    ok, _ = is_scalp_row({"source": "telegram", "reason": "Cornix signal ETH long"})
    assert ok is False


def test_analyze_scalp_signals_from_fixture(tmp_path: Path):
    ledger = tmp_path / "ledger"
    trades = tmp_path / "trades"
    learning = tmp_path / "learning"
    ledger.mkdir(parents=True)
    trades.mkdir(parents=True)
    learning.mkdir(parents=True)

    ledger_row = {
        "id": "abc1",
        "symbol": "SOLUSDT",
        "side": "Buy",
        "confidence": 0.8,
        "source": "SPIKE_SCANNER",
        "status": "skipped",
        "reason": "spike_scalp_pump",
        "created_at": "2099-01-01T12:00:00+00:00",
        "raw": {"scanner_kind": "spike_scalp"},
    }
    trade_row = {
        "event": "closed",
        "symbol": "SOLUSDT",
        "side": "Buy",
        "origin": "bot",
        "source": "SPIKE_SCANNER",
        "reason": "spike_scalp_pump",
        "pnl_usdt": 1.25,
        "ts": "2099-01-01T13:00:00+00:00",
    }
    (ledger / "signal_ledger.jsonl").write_text(
        json.dumps(ledger_row) + "\n", encoding="utf-8"
    )
    (trades / "trade_history.jsonl").write_text(
        json.dumps(trade_row) + "\n", encoding="utf-8"
    )

    report = analyze_scalp_signals(tmp_path, hours=24 * 365)
    assert report.ledger_signals.total == 1
    assert report.real_trades.real_trades == 1
    assert report.real_trades.real_pnl_usdt == 1.25
    md = format_scalp_report_md(report)
    assert "spike_scanner" in md


def test_spike_scalp_config_notes_enabled():
    cfg = {
        "market_scanner": {
            "enabled": True,
            "spike_scalp": {"enabled": True, "min_move_pct": 4.0, "execute_min_score": 72},
        },
        "telegram_signal_agent": {"market_scanner_enabled": True},
    }
    notes = spike_scalp_config_notes(cfg)
    assert any("spike_scalp OK" in n for n in notes)


def test_spike_scalp_config_notes_disabled():
    cfg = {"market_scanner": {"enabled": True, "spike_scalp": {"enabled": False}}}
    notes = spike_scalp_config_notes(cfg)
    assert any("enabled=false" in n for n in notes)


def test_external_sentiment_has_no_adanos():
    import inspect

    from prd_agent.signals import external_sentiment_agent as mod

    src = inspect.getsource(mod)
    assert "adanos" not in src.lower()
    assert "aiohttp" not in src


def test_production_config_has_spike_scalp_no_adanos():
    root = Path(__file__).resolve().parents[2]
    text = (root / "deploy" / "config.production.yaml").read_text(encoding="utf-8")
    assert "spike_scalp:" in text
    assert "enabled: true" in text.split("spike_scalp:")[1].split("scanner_reversal_exit")[0]
    assert "adanos:" not in text
    assert "ADANOS_API_KEY" not in text
