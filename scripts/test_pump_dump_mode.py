#!/usr/bin/env python3
"""
Проверка режима pump/dump без биржи.
Запуск на сервере:
  cd /root/PRD-BOT-ALL
  ./venv/bin/python3 scripts/test_pump_dump_mode.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.risk.pullback_entry import check_pullback_entry
from prd_agent.signals.pump_dump_mode import (
    TrailingProfile,
    is_pump_dump_signal,
)
from prd_agent.signals.types import UnifiedSignal


def ok(msg: str) -> None:
    print(f"  OK: {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    raise SystemExit(1)


def main() -> None:
    print("=== Тест режима pump/dump (без Bybit) ===\n")

    pump = UnifiedSignal(
        symbol="TESTUSDT",
        side="Buy",
        confidence=0.88,
        source="mirror_pump_dump_agent",
        reason="pattern_score=0.85 fast-exec",
    )
    normal = UnifiedSignal(
        symbol="BTCUSDT",
        side="Buy",
        confidence=0.9,
        source="own_multi_agent",
    )

    if not is_pump_dump_signal(pump):
        fail("mirror_pump_dump не распознан")
    ok("сигнал Mirror pump/dump распознан")

    if is_pump_dump_signal(normal):
        fail("обычный сигнал ошибочно как pump/dump")
    ok("обычный сигнал не pump/dump")

    cfg = {
        "pullback_entry": {"enabled": True, "momentum_bars": 5},
        "pump_dump_trade": {"enabled": True},
        "positions": {
            "trailing_activation_pct": 1.35,
            "trailing_distance_pct": 1.55,
            "pump_dump_trailing": {
                "trailing_activation_pct": 0.42,
                "trailing_distance_pct": 0.52,
            },
        },
    }
    klines_chase = [{"close": 1.0 + i * 0.02} for i in range(10)]
    pb_pump, reason_pump = check_pullback_entry(pump, klines_chase, cfg)
    if not pb_pump or "pump_dump" not in reason_pump:
        fail(f"pullback для pump/dump: {reason_pump}")
    ok(f"pullback пропущен: {reason_pump}")

    pb_norm, reason_norm = check_pullback_entry(normal, klines_chase, cfg)
    if pb_norm:
        fail("обычный сигнал должен блокироваться pullback при импульсе")
    ok(f"обычный сигнал ждёт откат: {reason_norm[:60]}...")

    p = cfg["positions"]
    slow = TrailingProfile.from_positions_cfg(p)
    fast = TrailingProfile.from_positions_cfg(p, subsection="pump_dump_trailing")
    if fast.activation_pct >= slow.activation_pct:
        fail(
            f"быстрый трейлинг не быстрее: fast={fast.activation_pct} slow={slow.activation_pct}"
        )
    ok(
        f"трейлинг: обычный activation={slow.activation_pct}% | "
        f"pump/dump activation={fast.activation_pct}%"
    )

    print("\n=== Все проверки пройдены ===")
    print("Дальше: тест с живым Mirror (логи trading_bot + copy_mirror).")


if __name__ == "__main__":
    main()
