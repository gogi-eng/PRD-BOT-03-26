"""Не учитывать одну и ту же закрытую сделку Bybit дважды (в т.ч. после перезапуска)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Set


class ClosedPnlDedup:
    def __init__(self, path: Path, *, keep_days: int = 3):
        self.path = path
        self.keep_days = max(1, int(keep_days))
        self._seen: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._seen = {str(k): str(v) for k, v in raw.items()}
        except (json.JSONDecodeError, OSError):
            self._seen = {}
        self._prune_old()

    def _prune_old(self) -> None:
        cutoff = date.today().isoformat()
        # ключи без даты оставляем; со датой старше keep_days удаляем
        today = datetime.now(timezone.utc).date()
        out: Dict[str, str] = {}
        for oid, ds in self._seen.items():
            try:
                d = date.fromisoformat(ds[:10])
            except ValueError:
                out[oid] = ds
                continue
            if (today - d).days <= self.keep_days:
                out[oid] = ds
        self._seen = out

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._seen, ensure_ascii=False), encoding="utf-8")

    def is_new(self, order_id: str) -> bool:
        return bool(order_id) and order_id not in self._seen

    def mark(self, order_id: str) -> None:
        if not order_id:
            return
        self._seen[order_id] = datetime.now(timezone.utc).isoformat()
        if len(self._seen) % 20 == 0:
            self._prune_old()
        self._save()
