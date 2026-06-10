"""Пресеты риска: консервативный / нормальный / агрессивный."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

ALLOWED_PRESETS = ("conservative", "normal", "aggressive")


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def preset_patch(cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    block = cfg.get("risk_presets", {})
    if not isinstance(block, dict):
        block = {}
    patch = block.get(name)
    if not isinstance(patch, dict) or not patch:
        raise ValueError(f"Пресет '{name}' не найден в config risk_presets")
    return patch


def apply_risk_preset(cfg_path: Path, cfg: Dict[str, Any], name: str) -> Tuple[List[str], str]:
    """Записывает пресет в config.yaml. Возвращает (список изменений, путь backup)."""
    if name not in ALLOWED_PRESETS:
        raise ValueError(f"Неизвестный пресет: {name}")

    patch = preset_patch(cfg, name)
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    before = yaml.safe_dump(data, allow_unicode=True)
    merged = _deep_merge(data, patch)
    after = yaml.safe_dump(merged, allow_unicode=True)

    changes: List[str] = []
    if before != after:
        for section, values in patch.items():
            if isinstance(values, dict):
                for k, v in values.items():
                    changes.append(f"{section}.{k}={v}")

    backup = cfg_path.parent / "data" / "sandbox" / (
        f"config_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.yaml"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists():
        shutil.copy2(cfg_path, backup)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, default_flow_style=False)

    meta = merged.setdefault("risk_presets_meta", {})
    if isinstance(meta, dict):
        meta["active"] = name
        meta["applied_at"] = datetime.now(timezone.utc).isoformat()
        with cfg_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(merged, f, allow_unicode=True, default_flow_style=False)

    return changes, str(backup)
