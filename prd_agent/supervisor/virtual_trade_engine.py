"""
Виртуальные сделки по сигналам бота (SL/TP как у реального ордера).
Отслеживает, где бы закрылась сделка по графику (mark price).
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prd_agent.supervisor.virtual")


@dataclass
class VirtualTrade:
    id: str
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    source: str
    confidence: float
    status: str = "open"
    opened_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    closed_at: str = ""
    exit_price: float = 0.0
    close_reason: str = ""
    pnl_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    ledger_id: str = ""
    real_status: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VirtualTradeEngine:
    def __init__(self, store_dir: Path, *, max_open: int = 40, max_age_hours: float = 72):
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.open_path = self.store_dir / "virtual_open.json"
        self.closed_path = self.store_dir / "virtual_closed.jsonl"
        self.max_open = max(5, int(max_open))
        self.max_age_hours = float(max_age_hours)

    def _load_open(self) -> Dict[str, VirtualTrade]:
        if not self.open_path.exists():
            return {}
        try:
            raw = json.loads(self.open_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        out: Dict[str, VirtualTrade] = {}
        for sym, row in (raw.get("by_symbol") or {}).items():
            try:
                out[str(sym).upper()] = VirtualTrade(**row)
            except TypeError:
                pass
        return out

    def _save_open(self, open_map: Dict[str, VirtualTrade]) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "by_symbol": {k: v.to_dict() for k, v in open_map.items()},
        }
        self.open_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _append_closed(self, trade: VirtualTrade) -> None:
        with self.closed_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trade.to_dict(), ensure_ascii=False) + "\n")

    @staticmethod
    def _pnl_pct(side: str, entry: float, price: float) -> float:
        if entry <= 0:
            return 0.0
        if str(side).lower() == "buy":
            return (price - entry) / entry * 100
        return (entry - price) / entry * 100

    def open_trade(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        source: str,
        confidence: float,
        ledger_id: str = "",
        real_status: str = "received",
    ) -> Optional[VirtualTrade]:
        sym = symbol.upper()
        if entry <= 0 or stop_loss <= 0 or take_profit <= 0:
            return None
        open_map = self._load_open()
        if sym in open_map:
            return None
        if len(open_map) >= self.max_open:
            return None
        vt = VirtualTrade(
            id=uuid.uuid4().hex[:12],
            symbol=sym,
            side=side,
            entry=float(entry),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            source=source,
            confidence=float(confidence),
            ledger_id=ledger_id,
            real_status=real_status,
        )
        open_map[sym] = vt
        self._save_open(open_map)
        logger.info(
            "VIRTUAL OPEN %s %s entry=%.6g SL=%.6g TP=%.6g (%s)",
            sym,
            side,
            entry,
            stop_loss,
            take_profit,
            source,
        )
        return vt

    def mark_real_status(self, ledger_id: str, status: str, reason: str = "") -> None:
        open_map = self._load_open()
        for vt in open_map.values():
            if vt.ledger_id == ledger_id:
                vt.real_status = f"{status}:{reason[:80]}" if reason else status
                self._save_open(open_map)
                return

    async def tick(self, exchange) -> List[VirtualTrade]:
        """Проверка mark price: закрытие по SL/TP."""
        open_map = self._load_open()
        if not open_map:
            return []
        now = datetime.now(timezone.utc)
        closed_now: List[VirtualTrade] = []
        for sym, vt in list(open_map.items()):
            try:
                opened = datetime.fromisoformat(vt.opened_at.replace("Z", "+00:00"))
                age_h = (now - opened).total_seconds() / 3600
                if age_h > self.max_age_hours:
                    vt.status = "expired"
                    vt.close_reason = "max_age"
                    vt.closed_at = now.isoformat()
                    vt.exit_price = await exchange.get_price(sym)
                    vt.pnl_pct = self._pnl_pct(vt.side, vt.entry, vt.exit_price)
                    closed_now.append(vt)
                    del open_map[sym]
                    self._append_closed(vt)
                    continue
                price = await exchange.get_price(sym)
            except Exception as exc:
                logger.debug("virtual tick %s: %s", sym, exc)
                continue
            pnl = self._pnl_pct(vt.side, vt.entry, price)
            vt.mfe_pct = max(vt.mfe_pct, pnl)
            vt.mae_pct = min(vt.mae_pct, pnl)
            is_buy = str(vt.side).lower() == "buy"
            hit_sl = (is_buy and price <= vt.stop_loss) or (
                not is_buy and price >= vt.stop_loss
            )
            hit_tp = (is_buy and price >= vt.take_profit) or (
                not is_buy and price <= vt.take_profit
            )
            if hit_sl or hit_tp:
                vt.status = "closed_tp" if hit_tp else "closed_sl"
                vt.close_reason = "take_profit" if hit_tp else "stop_loss"
                vt.closed_at = now.isoformat()
                vt.exit_price = price
                vt.pnl_pct = pnl
                closed_now.append(vt)
                del open_map[sym]
                self._append_closed(vt)
                logger.info(
                    "VIRTUAL CLOSE %s %s %s pnl=%.2f%% exit=%.6g",
                    sym,
                    vt.side,
                    vt.close_reason,
                    vt.pnl_pct,
                    price,
                )
        self._save_open(open_map)
        return closed_now

    def stats(self, hours: float) -> Dict[str, Any]:
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        closed: List[Dict[str, Any]] = []
        if self.closed_path.exists():
            for line in self.closed_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    ts = datetime.fromisoformat(
                        str(row.get("closed_at", row.get("opened_at", ""))).replace(
                            "Z", "+00:00"
                        )
                    )
                    if ts.timestamp() >= cutoff:
                        closed.append(row)
                except (json.JSONDecodeError, ValueError, KeyError):
                    pass
        wins = sum(1 for r in closed if float(r.get("pnl_pct", 0)) >= 0)
        losses = len(closed) - wins
        open_n = len(self._load_open())
        return {
            "hours": hours,
            "closed": len(closed),
            "open": open_n,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / len(closed) * 100, 1) if closed else 0.0,
            "avg_pnl_pct": round(
                sum(float(r.get("pnl_pct", 0)) for r in closed) / max(len(closed), 1),
                3,
            ),
        }
