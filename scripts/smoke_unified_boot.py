#!/usr/bin/env python3
"""Проверка старта unified-бота без systemd (импорты + config + опционально один цикл)."""
from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _run_cycle_smoke(orch) -> None:
    print("CYCLE: running _cycle() once...")
    await orch._cycle()
    print("OK _cycle() completed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test unified bot boot")
    parser.add_argument(
        "--cycle",
        action="store_true",
        help="Выполнить один торговый цикл (_cycle) после инициализации",
    )
    args = parser.parse_args()

    print("ROOT:", ROOT)
    try:
        from prd_agent.engine.orchestrator import UnifiedOrchestrator

        print("OK import UnifiedOrchestrator")
    except Exception as exc:
        print("FAIL import orchestrator:", exc)
        traceback.print_exc()
        return 1

    try:
        from prd_agent.config import load_config
        from prd_agent.config_validate import validate_config_data

        cfg = load_config(ROOT / "config.yaml")
        ok, errs = validate_config_data(cfg)
        if not ok:
            print("FAIL config validate:")
            for e in errs:
                print(" ", e)
            return 2
        print("OK config.yaml")
    except Exception as exc:
        print("FAIL config:", exc)
        traceback.print_exc()
        return 2

    try:
        orch = UnifiedOrchestrator(cfg)
        print("OK UnifiedOrchestrator() init")
        print("  symbols:", len(orch.symbols))
    except Exception as exc:
        print("FAIL orchestrator init:", exc)
        traceback.print_exc()
        return 3

    if args.cycle:
        try:
            asyncio.run(_run_cycle_smoke(orch))
        except Exception as exc:
            print("FAIL cycle:", exc)
            traceback.print_exc()
            return 4
        try:
            asyncio.run(orch.close())
        except Exception as exc:
            print("WARN close:", exc)

    print("SMOKE OK — можно restart systemd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
