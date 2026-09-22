"""Unit-тесты логики timesfm_bybit_compare (без загрузки модели)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.timesfm_bybit_compare import (
    actual_move_pct,
    build_context_closes,
    compare_signals,
    direction_from_delta,
    find_kline_index,
    load_klines_cache,
    load_ledger_skipped,
    load_trade_history_entries,
    resolve_signals,
    save_klines_cache,
    side_to_direction,
)


def test_side_and_direction_helpers() -> None:
    assert side_to_direction("Buy") == "up"
    assert side_to_direction("SELL") == "down"
    assert direction_from_delta(0.5) == "up"
    assert direction_from_delta(-0.5) == "down"
    assert direction_from_delta(0.001) == "flat"


def test_find_kline_index_and_context() -> None:
    base = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    klines = [
        {"timestamp": base + i * 900_000, "close": 100.0 + i}
        for i in range(10)
    ]
    idx = find_kline_index(klines, base + 4 * 900_000)
    assert idx == 4
    ctx = build_context_closes(klines, idx, context_len=3)
    assert list(ctx) == [102.0, 103.0, 104.0]


def test_actual_move_pct() -> None:
    assert actual_move_pct([100.0, 101.0, 102.0], horizon=2) == 2.0


def test_load_trade_history_entries(tmp_path: Path) -> None:
    p = tmp_path / "th.jsonl"
    row = {
        "event": "entered",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "entry": 65000.0,
        "order_id": "abc123",
        "source": "own_multi_agent",
        "ts": "2026-06-15T10:00:00+00:00",
    }
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    sigs = load_trade_history_entries(p, "BTCUSDT", limit=5)
    assert len(sigs) == 1
    assert sigs[0].side == "Buy"


def test_klines_cache_roundtrip(tmp_path: Path) -> None:
    klines = [{"timestamp": 1000, "close": 100.0, "high": 101, "low": 99}]
    p = tmp_path / "cache.json"
    save_klines_cache(p, symbol="BTCUSDT", interval="15", klines=klines)
    loaded, meta = load_klines_cache(p)
    assert len(loaded) == 1
    assert meta["symbol"] == "BTCUSDT"


def test_load_ledger_skipped(tmp_path: Path) -> None:
    p = tmp_path / "ledger.jsonl"
    row = {
        "id": "abc",
        "symbol": "BTCUSDT",
        "side": "Sell",
        "status": "skipped",
        "reason": "quality_gate: RR",
        "created_at": "2026-06-15T10:00:00+00:00",
        "source": "own_multi_agent",
    }
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    sigs = load_ledger_skipped(p, "BTCUSDT", 5)
    assert len(sigs) == 1
    assert sigs[0].origin == "signal_ledger"


def test_resolve_signals_priority(tmp_path: Path) -> None:
    skipped = tmp_path / "skipped.jsonl"
    skipped.write_text(
        json.dumps(
            {
                "ledger_id": "s1",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "entry": 1,
                "signal_at": "2026-06-15T10:00:00+00:00",
                "source": "spike",
                "outcome": "take_profit",
                "pnl_pct": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sigs, src = resolve_signals(
        symbol="BTCUSDT",
        limit=5,
        skipped_file=skipped,
        ledger_file=None,
        trade_history=tmp_path / "missing.jsonl",
    )
    assert len(sigs) == 1
    assert "skipped_backtest" in src


def test_compare_signals_dry_run() -> None:
    base = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    klines = [
        {
            "timestamp": base + i * 900_000,
            "close": 100.0 + i * 0.1,
            "high": 101.0,
            "low": 99.0,
        }
        for i in range(200)
    ]
    from scripts.timesfm_bybit_compare import SignalRow

    sig = SignalRow(
        signal_id="t1",
        symbol="BTCUSDT",
        side="Buy",
        entry=100.0,
        signal_at_ms=base + 50 * 900_000,
        source="test",
        origin="trade_history",
    )
    rep = compare_signals(
        symbol="BTCUSDT",
        interval="15",
        horizon=8,
        context_len=64,
        klines=klines,
        signals=[sig],
        model=None,
        dry_run=True,
    )
    assert rep.signals_total == 1
    assert len(rep.rows) == 1
    assert rep.rows[0].tfm_direction == "n/a"
