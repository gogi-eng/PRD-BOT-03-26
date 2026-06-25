"""Тесты лёгкого снимка в signal_ledger."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.analysis.signal_ledger import SignalLedger, SignalStatus


def test_ledger_record_stores_snapshot(tmp_path: Path) -> None:
    ledger = SignalLedger(tmp_path)
    snap = {
        "atr_pct": 0.42,
        "rsi": 58.0,
        "normalized_imbalance": 0.11,
        "spread_pct": 0.02,
    }
    entry = ledger.record(
        symbol="ETHUSDT",
        side="BUY",
        confidence=0.91,
        source="agent:trend",
        status=SignalStatus.SKIPPED,
        reason="quality_gate: confidence",
        entry=2500.0,
        stop_loss=2450.0,
        take_profit=2600.0,
        snapshot=snap,
    )
    assert entry.snapshot == snap

    lines = (tmp_path / "signal_ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    row = json.loads(lines[0])
    assert row["snapshot"]["rsi"] == 58.0
    assert row["status"] == "skipped"


def test_build_light_signal_snapshot_from_klines() -> None:
    import asyncio

    from prd_agent.analysis.entry_snapshot import build_light_signal_snapshot

    class FakeExchange:
        async def get_klines(self, symbol: str, interval: str, limit: int):
            base = 100.0
            out = []
            for i in range(30):
                c = base + i * 0.1
                out.append(
                    {
                        "open": c - 0.05,
                        "high": c + 0.2,
                        "low": c - 0.2,
                        "close": c,
                        "volume": 1000 + i,
                    }
                )
            return out

    snap = asyncio.run(
        build_light_signal_snapshot(
            exchange=FakeExchange(),
            cfg={"_root": ".", "timezone_offset": 3},
            symbol="BTCUSDT",
            side="BUY",
            entry=103.0,
        )
    )
    assert snap["symbol"] == "BTCUSDT"
    assert snap["entry"] == 103.0
    assert "ts" in snap
    assert "local_hour" in snap
