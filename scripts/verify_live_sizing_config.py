#!/usr/bin/env python3
"""Проверка ×1.5 sizing в live config.yaml после install_*_config.sh.

Почему нужно: install копирует deploy→config, но SelfImprover (auto_apply_low_risk)
раньше мог снова снизить trading.risk_pct_per_trade до 0.1. Этот скрипт падает,
если live не совпадает с ожидаемым baseline.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Baseline после ×1.5 (01.08.26) + dynamic_leverage.min≥10 (02.08.26).
EXPECTED = {
    "production": {
        "risk_pct_per_trade": 0.225,
        "min_risk_pct": 0.15,
        "max_notional_balance_pct": 120.0,
        "dynamic_leverage_min": 10,
        "presets": (0.375, 0.525, 0.675),
    },
    "agent_world": {
        "risk_pct_per_trade": 0.225,
        "min_risk_pct": 0.15,
        "max_notional_balance_pct": 45.0,
        "dynamic_leverage_min": 10,
        "presets": (0.375, 0.525, 0.675),
    },
}


def _fail(msg: str) -> None:
    print(f"FAIL sizing: {msg}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=sorted(EXPECTED),
        required=True,
        help="production или agent_world",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Путь к config.yaml (по умолчанию: <repo>/config.yaml)",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cfg_path = args.config or (root / "config.yaml")
    if not cfg_path.is_file():
        _fail(f"нет файла {cfg_path}")
        return 1

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    exp = EXPECTED[args.profile]
    ok = True

    trading = cfg.get("trading") or {}
    risk = float(trading.get("risk_pct_per_trade", 0) or 0)
    if abs(risk - exp["risk_pct_per_trade"]) > 1e-9:
        _fail(f"trading.risk_pct_per_trade={risk} (ожидалось {exp['risk_pct_per_trade']})")
        ok = False

    lev_min = int((trading.get("dynamic_leverage") or {}).get("min", 0) or 0)
    if lev_min < int(exp["dynamic_leverage_min"]):
        _fail(f"trading.dynamic_leverage.min={lev_min} (ожидалось >= {exp['dynamic_leverage_min']})")
        ok = False

    min_risk = float((cfg.get("supervisor_v4") or {}).get("min_risk_pct", 0) or 0)
    if abs(min_risk - exp["min_risk_pct"]) > 1e-9:
        _fail(f"supervisor_v4.min_risk_pct={min_risk} (ожидалось {exp['min_risk_pct']})")
        ok = False

    notion = float(
        (cfg.get("telegram_signal_agent") or {}).get("max_notional_balance_pct", 0) or 0
    )
    if abs(notion - exp["max_notional_balance_pct"]) > 1e-9:
        _fail(
            f"telegram_signal_agent.max_notional_balance_pct={notion} "
            f"(ожидалось {exp['max_notional_balance_pct']})"
        )
        ok = False

    presets = cfg.get("risk_presets") or {}
    got = (
        float((presets.get("conservative") or {}).get("trading", {}).get("risk_pct_per_trade", 0) or 0),
        float((presets.get("normal") or {}).get("trading", {}).get("risk_pct_per_trade", 0) or 0),
        float((presets.get("aggressive") or {}).get("trading", {}).get("risk_pct_per_trade", 0) or 0),
    )
    if got != tuple(float(x) for x in exp["presets"]):
        _fail(f"risk_presets risk_pct={got} (ожидалось {exp['presets']})")
        ok = False

    if not ok:
        return 1
    print(
        f"OK sizing [{args.profile}]: risk={risk} lev_min={lev_min} "
        f"min_risk={min_risk} notional={notion} presets={got}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
