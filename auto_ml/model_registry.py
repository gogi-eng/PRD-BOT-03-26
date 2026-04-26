#!/usr/bin/env python3
"""Append-only model registry JSON (paths + metrics)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class ModelRegistry:
    def __init__(self, path: str = "models/registry.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def _load(self) -> List[Dict[str, Any]]:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: List[Dict[str, Any]]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def register(self, model_path: str, metrics: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> None:
        rec: Dict[str, Any] = {
            "model_path": model_path,
            "metrics": metrics,
            "timestamp": time.time(),
        }
        if extra:
            rec["extra"] = extra
        data = self._load()
        data.append(rec)
        self._save(data)

    def best(self, key: str = "sharpe_like") -> Optional[Dict[str, Any]]:
        data = self._load()
        if not data:
            return None

        def score(rec: Dict[str, Any]) -> float:
            m = rec.get("metrics") or {}
            if key == "sharpe_like":
                mean = float(m.get("pnl_mean", 0.0) or 0.0)
                std = float(m.get("pnl_std", 1.0) or 1.0)
                return mean / max(std, 1e-9)
            if key == "pnl_mean":
                return float(m.get("pnl_mean", 0.0) or 0.0)
            return float(m.get("pnl_mean", 0.0) or 0.0) - float(m.get("pnl_std", 0.0) or 0.0)

        return max(data, key=score)

    def last(self) -> Optional[Dict[str, Any]]:
        data = self._load()
        return data[-1] if data else None
