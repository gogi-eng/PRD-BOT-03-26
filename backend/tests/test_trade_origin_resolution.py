"""Классификация bot vs manual в статистике и при закрытии с биржи."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prd_agent.analysis.trade_analytics import build_report
from prd_agent.positions.bot_position_registry import (
    origin_for_open_symbol,
    register_bot_open,
    resolve_closed_origin,
)


def test_origin_for_open_symbol_matches_order_id(tmp_path: Path):
    journal = tmp_path / "trade_history.jsonl"
    rows = [
        {
            "event": "entered",
            "symbol": "BTCUSDT",
            "source": "own_multi_agent",
            "order_id": "enter-1",
            "ts": "2026-06-10T03:00:00+00:00",
        },
        {
            "event": "closed",
            "symbol": "BTCUSDT",
            "origin": "manual",
            "order_id": "close-1",
            "ts": "2026-06-10T04:00:00+00:00",
        },
    ]
    journal.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert origin_for_open_symbol(journal, "BTCUSDT", order_id="close-1") == "bot"


def test_resolve_closed_origin_uses_registry_before_unregister(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    register_bot_open(data_dir, "ETHUSDT", source="own_multi_agent")
    origin = resolve_closed_origin(
        data_dir,
        "ETHUSDT",
        order_id="x",
        journal_path=tmp_path / "missing.jsonl",
    )
    assert origin == "bot"


def test_build_report_shows_bot_not_manual(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    trades_dir = data_dir / "trades"
    trades_dir.mkdir()
    journal = trades_dir / "trade_history.jsonl"
    journal.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "event": "entered",
                    "symbol": "CRVUSDT",
                    "source": "own_multi_agent",
                    "order_id": "e1",
                    "ts": "2026-06-11T10:00:00+00:00",
                },
                {
                    "event": "closed",
                    "symbol": "CRVUSDT",
                    "pnl": -1.0,
                    "reason": "exchange_closed",
                    "origin": "manual",
                    "order_id": "c1",
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            ]
        ),
        encoding="utf-8",
    )
    text = build_report(journal, hours=24, data_dir=data_dir)
    assert "Бот:" in text or "• Бот:" in text
    assert "Ручные: n=1" not in text
