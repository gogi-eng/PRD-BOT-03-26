"""
Риск-менеджер (упрощённая версия engine/risk_manager.py из PRD-BOT-03-26).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Dict, Optional, Tuple


class GuardStatus(Enum):
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    EMERGENCY = "EMERGENCY"


@dataclass
class DayStats:
    date: date
    trades: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl_usdt: float = 0.0
    net_pnl_pct: float = 0.0
    consecutive_losses: int = 0


class RiskGuard:
    def __init__(self, cfg: Dict, initial_balance: float = 0.0):
        r = cfg.get("risk", {})
        self.max_consecutive_losses = int(r.get("max_consecutive_losses", 4))
        self.max_daily_loss_pct = float(r.get("max_daily_loss_pct", 5.0))
        self.max_daily_loss_usdt = float(r.get("max_daily_loss_usdt", 100.0))
        self.max_trades_per_day = int(r.get("max_trades_per_day", 20))
        self.max_positions = int(cfg.get("trading", {}).get("max_positions", 3))
        self.cooldown_after_loss_sec = int(r.get("cooldown_after_loss_sec", 300))
        self.cooldown_after_stop_hours = int(r.get("cooldown_after_stop_hours", 2))
        self.initial_balance = initial_balance
        self.reduce_after_losses = int(r.get("reduce_after_losses", 2))
        self.reduction_factor = float(r.get("reduction_factor", 0.5))

        self.status = GuardStatus.ACTIVE
        self.stop_reason = ""
        self.day_stats = DayStats(date=date.today())
        self.last_loss_time: Optional[datetime] = None
        self.auto_stop_time: Optional[datetime] = None
        self._consecutive_losses = 0
        self._notify: Optional[Callable[[str], None]] = None
        self.open_positions_count = 0

    def set_notify_callback(self, cb: Callable[[str], None]) -> None:
        self._notify = cb

    def _ensure_today(self) -> None:
        today = date.today()
        if self.day_stats.date != today:
            self.day_stats = DayStats(date=today)
            self._consecutive_losses = 0
            if self.status == GuardStatus.STOPPED:
                self.status = GuardStatus.ACTIVE
                self.stop_reason = ""

    def record_trade(self, pnl: float) -> None:
        self._ensure_today()
        s = self.day_stats
        s.trades += 1
        s.net_pnl_usdt += pnl
        if self.initial_balance > 0:
            s.net_pnl_pct = (s.net_pnl_usdt / self.initial_balance) * 100
        if pnl > 0:
            s.wins += 1
            self._consecutive_losses = 0
        else:
            s.losses += 1
            self._consecutive_losses += 1
            s.consecutive_losses = self._consecutive_losses
            self.last_loss_time = datetime.now(timezone.utc)
        self._check_auto_stop()

    def _check_auto_stop(self) -> None:
        if self._consecutive_losses >= self.max_consecutive_losses:
            self._trigger_stop(f"{self._consecutive_losses} убытков подряд")
        elif self.max_daily_loss_pct > 0 and self.day_stats.net_pnl_pct <= -self.max_daily_loss_pct:
            self._trigger_stop(f"Дневной убыток {self.day_stats.net_pnl_pct:.1f}%")
        elif self.max_daily_loss_usdt > 0 and self.day_stats.net_pnl_usdt <= -self.max_daily_loss_usdt:
            self._trigger_stop(f"Дневной убыток ${self.day_stats.net_pnl_usdt:.2f}")
        elif self.max_trades_per_day > 0 and self.day_stats.trades >= self.max_trades_per_day:
            self._trigger_stop(f"Лимит сделок: {self.day_stats.trades}")

    def _trigger_stop(self, reason: str) -> None:
        if self.status not in (GuardStatus.STOPPED, GuardStatus.EMERGENCY):
            self.status = GuardStatus.STOPPED
            self.stop_reason = reason
            self.auto_stop_time = datetime.now(timezone.utc)
            if self._notify:
                self._notify(f"AUTO-STOP: {reason}")

    def can_trade(self, symbol: str = "") -> Tuple[bool, str]:
        self._ensure_today()
        if self.status == GuardStatus.EMERGENCY:
            return False, f"EMERGENCY: {self.stop_reason}"
        if self.status == GuardStatus.STOPPED and self.auto_stop_time:
            elapsed = (datetime.now(timezone.utc) - self.auto_stop_time).total_seconds()
            if elapsed < self.cooldown_after_stop_hours * 3600:
                return False, f"Пауза после стопа, осталось {int((self.cooldown_after_stop_hours * 3600 - elapsed) / 60)} мин"
            self.status = GuardStatus.ACTIVE
            self.stop_reason = ""
            self._consecutive_losses = 0
        if self.last_loss_time:
            elapsed = (datetime.now(timezone.utc) - self.last_loss_time).total_seconds()
            if elapsed < self.cooldown_after_loss_sec:
                return False, f"Кулдаун после убытка: {int(self.cooldown_after_loss_sec - elapsed)} сек"
        if self.open_positions_count >= self.max_positions:
            return False, f"Макс. позиций: {self.max_positions}"
        return True, ""

    def calculate_position_size(
        self, balance: float, risk_pct: float, entry: float, stop_loss: float, leverage: int
    ) -> float:
        if entry <= 0 or stop_loss <= 0 or balance <= 0:
            return 0.0
        mult = self.reduction_factor if self._consecutive_losses >= self.reduce_after_losses else 1.0
        risk_amount = balance * (risk_pct / 100) * mult
        distance = abs(entry - stop_loss)
        if distance <= 0:
            return 0.0
        qty = risk_amount / distance
        max_notional = balance * 0.1 * leverage
        max_qty = max_notional / entry
        return min(qty, max_qty)

    def snapshot(self) -> Dict:
        self._ensure_today()
        s = self.day_stats
        blocked = self.status != GuardStatus.ACTIVE
        return {
            "pnl_today_usdt": round(s.net_pnl_usdt, 2),
            "pnl_today_pct": round(s.net_pnl_pct, 2),
            "trades_today": s.trades,
            "consecutive_losses": self._consecutive_losses,
            "blocked": blocked,
            "block_reason": self.stop_reason if blocked else "",
            "status": self.status.value,
        }
