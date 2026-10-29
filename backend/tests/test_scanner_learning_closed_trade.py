"""Закрытая сделка сканера снова пишет урок и может снять летний режим stricter."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.market.scanner_learning import (
    decided_window,
    merge_learning_events,
    prefer_newer_filters,
    propose_adaptive_filters,
    record_closed_trade_lesson,
    resolve_scanner_close_source,
    window_signature,
)

_BOUNDS = {
    "score_floor": 60.0,
    "score_ceiling": 82.0,
    "vol_floor": 1.1,
    "vol_ceiling": 2.2,
    "range_floor": 2.2,
    "range_ceiling": 6.0,
    "atr_floor": 0.35,
    "atr_ceiling": 1.1,
}
_BASELINE = {
    "min_score_to_notify": 60,
    "min_volume_ratio": 1.4,
    "max_range_pct": 4.5,
    "max_atr_pct": 0.75,
}


def _summer_events() -> list[dict]:
    rows = []
    for index in range(1, 22):
        rows.append(
            {
                "id": f"summer-{index}",
                "checked_at": "2026-07-07T12:00:00+00:00",
                "symbol": "OLDUSDT",
                "scenario": "PUMP",
                "outcome": "win" if index <= 3 else "loss",
            }
        )
    return rows


def _stricter_filters(events: list[dict]) -> dict:
    decided = decided_window(events, 30)
    return {
        "min_score_to_notify": 63,
        "min_volume_ratio": 1.5,
        "max_range_pct": 4.25,
        "max_atr_pct": 0.71,
        "updated_at": "2026-10-24T06:32:09+00:00",
        "mode": "stricter",
        "sample": 21,
        "wins": 3,
        "losses": 18,
        "winrate": 0.1429,
        "learning_window_signature": window_signature(decided),
    }


def _state(events: list[dict], filters: dict) -> dict:
    return {
        "market_scanner_learning_events": list(events),
        "market_scanner_adaptive_filters": dict(filters),
    }


def _close(state: dict, *, order_id: str, source: str = "SPIKE_SCANNER", pnl: float = 1.25, window: int = 30) -> dict | None:
    event, _proposed = record_closed_trade_lesson(
        state,
        source=source,
        symbol="MAGMAUSDT",
        pnl=pnl,
        order_id=order_id,
        side="BUY",
        entry_price=10,
        exit_price=10.2,
        score=80,
        learning_window=window,
        min_events=6,
        baseline=_BASELINE,
        bounds=_BOUNDS,
        updated_at="2026-10-29T12:00:00+00:00",
    )
    return event


def test_closed_scanner_trade_appends_lesson_and_can_leave_summer_stricter() -> None:
    summer = _summer_events()
    filters = _stricter_filters(summer)
    assert (
        propose_adaptive_filters(
            events=summer,
            learning_window=30,
            min_events=6,
            baseline=_BASELINE,
            current_filters=filters,
            bounds=_BOUNDS,
            updated_at="2026-10-29T00:00:00+00:00",
        )
        is None
    )

    state = _state(summer, filters)
    added = _close(state, order_id="ord-new-1", window=30)
    assert added is not None
    events = state["market_scanner_learning_events"]
    assert [row["id"] for row in events[:21]] == [f"summer-{i}" for i in range(1, 22)]
    assert len(events) == 22
    assert added["id"] == "closed:ord-new-1"
    assert added["outcome"] == "win"
    assert added["source"] == "SPIKE_SCANNER"
    assert added["kind"] == "closed_trade"
    assert state["market_scanner_adaptive_filters"]["mode"] == "stricter"
    assert state["market_scanner_adaptive_filters"]["min_score_to_notify"] == 63

    assert _close(state, order_id="manual-1", source="telegram_channel", pnl=-5) is None
    assert len(state["market_scanner_learning_events"]) == 22

    for index in range(2, 8):
        assert _close(state, order_id=f"ord-new-{index}", source="MARKET_SCANNER", window=6) is not None
    fresh = decided_window(state["market_scanner_learning_events"], 6)
    assert [row["outcome"] for row in fresh] == ["win"] * 6
    assert not any(str(row["id"]).startswith("summer-") for row in fresh)
    updated = state["market_scanner_adaptive_filters"]
    assert updated["mode"] == "softer"
    assert int(updated["min_score_to_notify"]) < 63
    assert float(updated["min_volume_ratio"]) < 1.5
    assert float(updated["max_range_pct"]) > 4.25
    assert [row["id"] for row in state["market_scanner_learning_events"][:21]] == [
        f"summer-{i}" for i in range(1, 22)
    ]


def test_neutral_recent_window_leaves_stricter_for_baseline() -> None:
    """Окно уже не летнее и не плохое: пороги возвращаются к базовым, не остаются 63."""
    events = []
    for index in range(6):
        events.append(
            {
                "id": f"fresh-{index}",
                "outcome": "win" if index < 3 else "loss",
                "checked_at": "2026-10-29T00:00:00+00:00",
                "symbol": "NEWUSDT",
                "scenario": "DUMP",
            }
        )
    proposed = propose_adaptive_filters(
        events=events,
        learning_window=6,
        min_events=6,
        baseline=_BASELINE,
        current_filters=_stricter_filters(_summer_events()),
        bounds=_BOUNDS,
        updated_at="2026-10-29T01:00:00+00:00",
    )
    assert proposed is not None
    assert proposed["mode"] == "baseline"
    assert proposed["min_score_to_notify"] == 60
    assert proposed["min_volume_ratio"] == 1.4
    assert proposed["max_range_pct"] == 4.5
    assert proposed["max_atr_pct"] == 0.75


def test_scanner_source_comes_from_journal_when_memory_is_empty(tmp_path: Path) -> None:
    journal = tmp_path / "trade_history.jsonl"
    journal.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "entered",
                        "symbol": "MAGMAUSDT",
                        "side": "BUY",
                        "source": "SPIKE_SCANNER",
                        "order_id": "ord-77",
                        "entry": 3.5,
                        "confidence": 81,
                    }
                ),
                json.dumps(
                    {
                        "event": "entered",
                        "symbol": "BTCUSDT",
                        "side": "SELL",
                        "source": "manual",
                        "order_id": "ord-manual",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    source, meta = resolve_scanner_close_source(
        None, journal, tmp_path / "data", "MAGMAUSDT", "ord-77"
    )
    assert source == "SPIKE_SCANNER"
    assert meta["confidence"] == 81
    assert float(meta["entry"]) == 3.5

    other, _meta = resolve_scanner_close_source(
        None, journal, tmp_path / "data", "BTCUSDT", "ord-manual"
    )
    assert other == ""


def test_other_process_cannot_drop_a_new_learning_event() -> None:
    summer = _summer_events()
    disk_events = summer + [
        {
            "id": "closed:from-disk",
            "outcome": "win",
            "symbol": "SOLUSDT",
            "scenario": "PUMP",
            "checked_at": "2026-10-29T02:00:00+00:00",
        }
    ]
    merged = merge_learning_events(summer, disk_events, 50)
    assert [row["id"] for row in merged[:21]] == [f"summer-{i}" for i in range(1, 22)]
    assert merged[-1]["id"] == "closed:from-disk"
    assert prefer_newer_filters(
        {"updated_at": "2026-10-24T06:32:09+00:00", "mode": "stricter"},
        {"updated_at": "2026-10-29T03:00:00+00:00", "mode": "softer"},
    )["mode"] == "softer"

    root = Path(__file__).resolve().parents[2]
    agent_src = (root / "scripts" / "telegram_signal_agent.py").read_text(encoding="utf-8")
    orch_src = (root / "prd_agent" / "engine" / "orchestrator.py").read_text(encoding="utf-8")
    assert "record_closed_trade_lesson(" in agent_src
    assert "merge_learning_events(" in agent_src
    assert "_record_closed_scanner_trade_for_learning(" in orch_src
    assert "channel_trade_search: false" in (root / "config.yaml").read_text(encoding="utf-8")
