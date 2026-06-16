#!/usr/bin/env python3
"""Проверка старта unified-бота без systemd (импорты + config)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    print("ROOT:", ROOT)
    try:
        from prd_agent.engine.orchestrator import UnifiedOrchestrator

        print("OK import UnifiedOrchestrator")
    except Exception as exc:
        print("FAIL import orchestrator:", exc)
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
        return 2

    try:
        orch = UnifiedOrchestrator(cfg)
        print("OK UnifiedOrchestrator() init")
        print("  symbols:", len(orch.symbols))
    except Exception as exc:
        print("FAIL orchestrator init:", exc)
        return 3

    print("SMOKE OK — можно restart systemd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
