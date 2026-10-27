"""Пресеты риска: консервативный / нормальный / агрессивный (запись в runtime_config_overrides.yaml)."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from prd_agent.config import (
    deep_merge_config,
    load_runtime_overrides,
    runtime_overrides_path,
    save_runtime_overrides,
)

ALLOWED_PRESETS = ("conservative", "normal", "aggressive")


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    return deep_merge_config(base, patch)


def preset_patch(cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    block = cfg.get("risk_presets", {})
    if not isinstance(block, dict):
        block = {}
    patch = block.get(name)
    if not isinstance(patch, dict) or not patch:
        raise ValueError(f"Пресет '{name}' не найден в config risk_presets")
    return patch


def apply_risk_preset(cfg_path: Path, cfg: Dict[str, Any], name: str) -> Tuple[List[str], str]:
    """Записывает пресет в data/runtime_config_overrides.yaml (config.yaml остаётся read-only)."""
    if name not in ALLOWED_PRESETS:
        raise ValueError(f"Неизвестный пресет: {name}")

    patch = preset_patch(cfg, name)
    base_data: Dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            base_data = yaml.safe_load(f) or {}

    ov_path = runtime_overrides_path(cfg_path, cfg=cfg)
    current_ov = load_runtime_overrides(ov_path)
    effective_before = _deep_merge(base_data, current_ov)
    effective_after = _deep_merge(effective_before, patch)

    before_s = yaml.safe_dump(effective_before, allow_unicode=True)
    after_s = yaml.safe_dump(effective_after, allow_unicode=True)

    changes: List[str] = []
    if before_s != after_s:
        for section, values in patch.items():
            if isinstance(values, dict):
                for k, v in values.items():
                    changes.append(f"{section}.{k}={v}")

    backup = cfg_path.parent / "data" / "sandbox" / (
        f"config_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.yaml"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    if ov_path.exists():
        shutil.copy2(ov_path, backup)
    else:
        save_runtime_overrides(backup, current_ov)

    merged_ov = _deep_merge(current_ov, patch)
    meta = merged_ov.setdefault("risk_presets_meta", {})
    if isinstance(meta, dict):
        meta["active"] = name
        meta["applied_at"] = datetime.now(timezone.utc).isoformat()
    save_runtime_overrides(ov_path, merged_ov)

    return changes, str(backup)
