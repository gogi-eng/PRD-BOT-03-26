#!/usr/bin/env python3
"""
Вливает в config.yaml блоки входа из deploy/config.production.yaml,
не трогая telegram_signal_agent и прочие локальные настройки.

Запуск на сервере:
  cd /root/PRD-BOT-ALL && ./venv/bin/python3 scripts/sync_entry_config.py
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Нужен PyYAML: ./venv/bin/pip install pyyaml")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "config.production.yaml"
CONFIG = ROOT / "config.yaml"

TOP_KEYS = ("bot", "entry", "adaptive_regime_presets")
TRADING_PATCH = {
    "min_own_agent_confidence": 0.82,
    "symbol_rescan_interval_sec": 900,
}
SIGNALS_PATCH = {"min_analysis_confidence": 0.88}
TA_PATCH = {"min_confidence": 0.82}


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _deep_update(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_update(dst[key], value)
        else:
            dst[key] = value


def main() -> int:
    if not DEPLOY.exists():
        print(f"Нет эталона: {DEPLOY}")
        return 1
    if not CONFIG.exists():
        print(f"Нет {CONFIG}")
        return 1

    deploy = _load(DEPLOY)
    cfg = _load(CONFIG)

    for key in TOP_KEYS:
        block = deploy.get(key)
        if isinstance(block, dict):
            cfg.setdefault(key, {})
            if isinstance(cfg[key], dict):
                _deep_update(cfg[key], block)
            else:
                cfg[key] = dict(block)

    cfg.setdefault("trading", {})
    if isinstance(cfg["trading"], dict):
        cfg["trading"].update(TRADING_PATCH)

    cfg.setdefault("signals", {})
    if isinstance(cfg["signals"], dict):
        cfg["signals"].update(SIGNALS_PATCH)

    cfg.setdefault("ta_scanner", {})
    if isinstance(cfg["ta_scanner"], dict):
        cfg["ta_scanner"].update(TA_PATCH)

    bak = CONFIG.with_name(f"config.yaml.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(CONFIG, bak)
    CONFIG.write_text(
        yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    entry = cfg.get("entry", {}) if isinstance(cfg.get("entry"), dict) else {}
    adaptive = cfg.get("adaptive_regime_presets", {}) if isinstance(cfg.get("adaptive_regime_presets"), dict) else {}
    trading = cfg.get("trading", {}) if isinstance(cfg.get("trading"), dict) else {}

    print(f"OK: {CONFIG}")
    print(f"Резервная копия: {bak.name}")
    print(f"  entry.volatility_floor_atr_pct = {entry.get('volatility_floor_atr_pct')}")
    print(f"  adaptive.trend_volatility_floor_atr_pct = {adaptive.get('trend_volatility_floor_atr_pct')}")
    print(f"  adaptive.range_volatility_floor_atr_pct = {adaptive.get('range_volatility_floor_atr_pct')}")
    print(f"  trading.min_own_agent_confidence = {trading.get('min_own_agent_confidence')}")
    print(f"  trading.symbol_rescan_interval_sec = {trading.get('symbol_rescan_interval_sec')}")
    print("")
    print("Дальше: systemctl restart trading_bot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
