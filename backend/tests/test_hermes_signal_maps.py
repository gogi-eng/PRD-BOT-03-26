"""Тесты карт сигналов Hermes."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.learning.hermes_signal_maps import (
    HermesSignalMapBuilder,
    simulate_trailing_on_candles,
    write_signal_maps_artifacts,
)


def _write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_simulate_trailing_buy_tp(tmp_path: Path) -> None:
    candles = [
        {"h": 101.0, "l": 99.5, "c": 100.5},
        {"h": 102.0, "l": 100.0, "c": 101.5},
        {"h": 103.0, "l": 101.0, "c": 102.5},
    ]
    out = simulate_trailing_on_candles(
        side="BUY",
        entry=100.0,
        stop_loss=98.0,
        take_profit=102.0,
        candles=candles,
        activation_pct=1.0,
        distance_pct=0.5,
    )
    assert out["simulated"] is True
    assert out["would_exit"] in ("take_profit", "trailing_stop")


def test_build_maps_joins_ledger_skipped_and_trade(tmp_path: Path) -> None:
    data = tmp_path / "data"
    ledger_id = "abc123"
    _write_jsonl(
        data / "ledger" / "signal_ledger.jsonl",
        [
            {
                "id": ledger_id,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "confidence": 0.93,
                "source": "agent:trend",
                "status": "executed",
                "reason": "ok",
                "entry": 100.0,
                "stop_loss": 98.0,
                "take_profit": 105.0,
                "created_at": "2026-06-24T12:00:00+00:00",
                "raw": {"rsi": 55, "adx": 28},
            }
        ],
    )
    _write_jsonl(
        data / "supervisor" / "skipped_backtest" / "results.jsonl",
        [],
    )
    _write_jsonl(
        data / "trades" / "trade_history.jsonl",
        [
            {
                "event": "entered",
                "ledger_id": ledger_id,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "ts": "2026-06-24T12:00:05+00:00",
                "entry_context": {
                    "atr_pct": 0.45,
                    "normalized_imbalance": 0.12,
                    "rsi": 55,
                },
                "entry_candles": [
                    {"h": 100.5, "l": 99.8, "c": 100.2},
                    {"h": 101.2, "l": 100.0, "c": 101.0},
                ],
            },
            {
                "event": "closed",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "pnl_usdt": 3.5,
                "pnl_pct": 1.2,
                "close_reason": "take_profit",
                "ts": "2026-06-24T13:00:00+00:00",
            },
        ],
    )

    maps = HermesSignalMapBuilder(data).build_maps(hours=168)
    assert len(maps) == 1
    m = maps[0]
    assert m.ledger_id == ledger_id
    assert m.real_trade.get("matched") is True
    assert m.entry_params["indicators"].get("rsi") == 55
    assert m.virtual_trailing.get("simulated") is True

    out_dir = tmp_path / "out"
    jsonl_p, md_p = write_signal_maps_artifacts(
        maps, out_dir, source_label="TEST", hours=168
    )
    assert jsonl_p.is_file()
    assert md_p.is_file()
    line = jsonl_p.read_text(encoding="utf-8").strip().splitlines()[0]
    row = json.loads(line)
    assert row["symbol"] == "BTCUSDT"
