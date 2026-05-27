"""Фильтры: небольшой профит + quality_gate перед зеркалом."""
from __future__ import annotations

from typing import Any, Dict, Tuple

from prd_agent.copy_mirror.position_math import normalize_side
from prd_agent.risk.guard import RiskGuard
from prd_agent.risk.quality_gate import QualityGate
from prd_agent.signals.types import UnifiedSignal


def profit_in_band(
    profit_pct: float,
    *,
    min_pct: float,
    max_pct: float,
) -> Tuple[bool, str]:
    if profit_pct < min_pct:
        return False, f"profit {profit_pct:.3f}% < min {min_pct:.3f}%"
    if profit_pct > max_pct:
        return False, f"profit {profit_pct:.3f}% > max {max_pct:.3f}% (поздно)"
    return True, ""


def watch_expired(first_seen_ts: float, max_watch_minutes: float) -> bool:
    if max_watch_minutes <= 0:
        return False
    import time

    age_min = (time.time() - first_seen_ts) / 60.0
    return age_min > max_watch_minutes


def build_mirror_signal(pos: Dict[str, Any], profit_pct: float) -> UnifiedSignal:
    """Синтетический сигнал для quality_gate (источник = copy_mirror)."""
    conf = min(0.95, 0.70 + profit_pct / 10.0)
    return UnifiedSignal(
        symbol=pos["symbol"],
        side=pos["side"],
        entry=float(pos["entry"]),
        stop_loss=float(pos.get("stop_loss") or 0),
        take_profit=float(pos.get("take_profit") or 0),
        confidence=conf,
        source="copy_mirror",
        raw={"profit_pct": profit_pct},
    )


class MirrorEntryFilters:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        m = cfg.get("copy_mirror", {})
        p = m.get("profit", {}) if isinstance(m.get("profit"), dict) else {}
        self.min_profit_pct = float(p.get("min_pct", 0.12))
        self.max_profit_pct = float(p.get("max_pct", 1.5))
        self.max_watch_minutes = float(p.get("max_watch_minutes", 45))

        t = m.get("trading", {}) if isinstance(m.get("trading"), dict) else {}
        self.risk_pct = float(t.get("risk_pct", 1.0))
        self.max_positions = int(t.get("max_positions", 3))
        self.leverage = int(t.get("leverage", 10))
        self.min_qty_usdt = float(t.get("min_notional_usdt", 5.0))

        gate_cfg = dict(cfg)
        q = dict(m.get("quality_gate", {}) or {})
        q.setdefault("enabled", True)
        q.setdefault("min_confidence", 0.68)
        gate_cfg["quality_gate"] = q
        gate_cfg["trading"] = {**t, "max_positions": self.max_positions}
        gate_cfg.setdefault("risk", m.get("risk", {}) if isinstance(m.get("risk"), dict) else {})
        self.quality = QualityGate(gate_cfg)
        self.risk = RiskGuard(gate_cfg)

    def check_profit_window(
        self, profit_pct: float, first_seen_ts: float
    ) -> Tuple[bool, str]:
        if watch_expired(first_seen_ts, self.max_watch_minutes):
            return False, "mirror: истёк лимит ожидания профита"
        return profit_in_band(
            profit_pct,
            min_pct=self.min_profit_pct,
            max_pct=self.max_profit_pct,
        )

    async def check_before_open(
        self,
        pos: Dict[str, Any],
        profit_pct: float,
        exchange,
        *,
        open_on_target: int,
    ) -> Tuple[bool, str]:
        ok, reason = self.check_profit_window(profit_pct, pos.get("first_seen_ts", 0))
        if not ok:
            return False, reason

        if open_on_target >= self.max_positions:
            return False, f"mirror: лимит позиций на субаккаунте {self.max_positions}"

        sig = build_mirror_signal(pos, profit_pct)
        entry = float(pos["entry"])
        sl = float(pos.get("stop_loss") or 0)
        tp = float(pos.get("take_profit") or 0)

        if sl <= 0 or tp <= 0:
            side = normalize_side(pos["side"])
            dist = entry * 0.01
            if side == "Buy":
                sl = entry - dist if sl <= 0 else sl
                tp = entry + dist * 2 if tp <= 0 else tp
            else:
                sl = entry + dist if sl <= 0 else sl
                tp = entry - dist * 2 if tp <= 0 else tp

        ok_q, reason_q = await self.quality.check(sig, exchange, entry=entry, sl=sl, tp=tp)
        if not ok_q:
            return False, reason_q

        self.risk.open_positions_count = open_on_target
        ok_r, reason_r = self.risk.can_trade(pos["symbol"])
        if not ok_r:
            return False, f"mirror: {reason_r}"

        return True, ""

    def calc_qty(
        self, balance: float, entry: float, stop_loss: float, leverage: int
    ) -> float:
        qty = self.risk.calculate_position_size(
            balance, self.risk_pct, entry, stop_loss, leverage
        )
        if entry > 0 and qty * entry < self.min_qty_usdt:
            return 0.0
        return qty
