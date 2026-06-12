"""
Журнал всех сигналов: получены, отклонены, исполнены, просрочены.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class SignalStatus(str, Enum):
    RECEIVED = "received"
    SKIPPED = "skipped"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class LedgerEntry:
    id: str
    symbol: str
    side: str
    confidence: float
    source: str
    status: str
    reason: str = ""
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    order_id: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SignalLedger:
    def __init__(self, store_dir: Path):
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.store_dir / "signal_ledger.jsonl"

    def record(
        self,
        *,
        symbol: str,
        side: str,
        confidence: float,
        source: str,
        status: SignalStatus,
        reason: str = "",
        entry: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        order_id: str = "",
        raw: Optional[Dict] = None,
        entry_id: Optional[str] = None,
    ) -> LedgerEntry:
        now = datetime.now(timezone.utc).isoformat()
        e = LedgerEntry(
            id=entry_id or uuid.uuid4().hex[:12],
            symbol=symbol,
            side=side,
            confidence=confidence,
            source=source,
            status=status.value,
            reason=reason,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            order_id=order_id,
            created_at=now,
            updated_at=now,
            raw=raw or {},
        )
        with self._file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
        return e

    def update_status(self, entry_id: str, status: SignalStatus, reason: str = "", order_id: str = "") -> None:
        if not self._file.exists():
            return
        lines = self._file.read_text(encoding="utf-8").splitlines()
        out: List[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                out.append(line)
                continue
            if row.get("id") == entry_id:
                row["status"] = status.value
                row["reason"] = reason or row.get("reason", "")
                if order_id:
                    row["order_id"] = order_id
                row["updated_at"] = datetime.now(timezone.utc).isoformat()
            out.append(json.dumps(row, ensure_ascii=False))
        self._file.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")

    def recent(self, hours: float = 24) -> List[Dict]:
        if not self._file.exists():
            return []
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        rows: List[Dict] = []
        for line in self._file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                if ts.timestamp() >= cutoff:
                    rows.append(row)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        return rows

    def summary(self, hours: float = 24) -> Dict[str, Any]:
        rows = self.recent(hours)
        by_status: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for r in rows:
            by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
            by_source[r.get("source", "?")] = by_source.get(r.get("source", "?"), 0) + 1
        skip_baseline: Dict[str, Any] = {}
        try:
            from prd_agent.telemetry.skip_baseline import skip_baseline_from_rows

            skip_baseline = skip_baseline_from_rows(rows, hours=hours)
        except Exception:
            pass
        return {
            "period_hours": hours,
            "total": len(rows),
            "by_status": by_status,
            "by_source": by_source,
            "not_opened": by_status.get("skipped", 0)
            + by_status.get("rejected", 0)
            + by_status.get("received", 0),
            "skip_baseline": skip_baseline,
        }
