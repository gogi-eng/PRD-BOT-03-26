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


class StopKind(Enum):
    NONE = "none"
    CONSECUTIVE = "consecutive"
    DAILY_LOSS = "daily_loss"
    TRADE_LIMIT = "trade_limit"


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
        # Дневной лимит: до полуночи UTC, а не «пауза 2–3 ч и снова стоп»
        self.daily_loss_blocks_until_next_day = bool(
            r.get("daily_loss_blocks_until_next_day", True)
        )
        self.initial_balance = initial_balance
        self.reduce_after_losses = int(r.get("reduce_after_losses", 2))
        self.reduction_factor = float(r.get("reduction_factor", 0.5))

        self.status = GuardStatus.ACTIVE
        self.stop_reason = ""
        self.stop_kind = StopKind.NONE
        self.day_stats = DayStats(date=self._today_utc())
        self.last_loss_time: Optional[datetime] = None
        self.auto_stop_time: Optional[datetime] = None
        self._consecutive_losses = 0
        self._notify: Optional[Callable[[str], None]] = None
        self.open_positions_count = 0

    @staticmethod
    def _today_utc() -> date:
        return datetime.now(timezone.utc).date()

    @staticmethod
    def _next_utc_midnight() -> datetime:
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).date()
        return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)

    def set_notify_callback(self, cb: Callable[[str], None]) -> None:
        self._notify = cb

    def _ensure_today(self) -> None:
        today = self._today_utc()
        if self.day_stats.date != today:
            self.day_stats = DayStats(date=today)
            self._consecutive_losses = 0
            self.last_loss_time = None
            if self.status in (GuardStatus.STOPPED,):
                self.status = GuardStatus.ACTIVE
                self.stop_reason = ""
                self.stop_kind = StopKind.NONE
                self.auto_stop_time = None

    def _daily_loss_exceeded(self) -> bool:
        s = self.day_stats
        if self.max_daily_loss_pct > 0 and s.net_pnl_pct <= -self.max_daily_loss_pct:
            return True
        if self.max_daily_loss_usdt > 0 and s.net_pnl_usdt <= -self.max_daily_loss_usdt:
            return True
        return False

    def _daily_loss_reason(self) -> str:
        s = self.day_stats
        if self.max_daily_loss_usdt > 0 and s.net_pnl_usdt <= -self.max_daily_loss_usdt:
            return f"Дневной лимит убытка ${abs(s.net_pnl_usdt):.2f} (лимит ${self.max_daily_loss_usdt:.0f})"
        return f"Дневной лимит убытка {abs(s.net_pnl_pct):.1f}% (лимит {self.max_daily_loss_pct:.0f}%)"

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
            self._trigger_stop(
                f"{self._consecutive_losses} убытков подряд",
                StopKind.CONSECUTIVE,
            )
        elif self._daily_loss_exceeded():
            self._trigger_stop(self._daily_loss_reason(), StopKind.DAILY_LOSS)
        elif self.max_trades_per_day > 0 and self.day_stats.trades >= self.max_trades_per_day:
            self._trigger_stop(f"Лимит сделок: {self.day_stats.trades}", StopKind.TRADE_LIMIT)

    def _trigger_stop(self, reason: str, kind: StopKind) -> None:
        if self.status not in (GuardStatus.STOPPED, GuardStatus.EMERGENCY):
            self.status = GuardStatus.STOPPED
            self.stop_reason = reason
            self.stop_kind = kind
            self.auto_stop_time = datetime.now(timezone.utc)
            if self._notify:
                self._notify(f"AUTO-STOP: {reason}")

    def _minutes_until_utc_reset(self) -> int:
        delta = self._next_utc_midnight() - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds() / 60))

    def can_trade(self, symbol: str = "") -> Tuple[bool, str]:
        self._ensure_today()

        if self.status == GuardStatus.EMERGENCY:
            return False, f"EMERGENCY: {self.stop_reason}"

        if self._daily_loss_exceeded():
            if self.daily_loss_blocks_until_next_day:
                mins = self._minutes_until_utc_reset()
                h, m = divmod(mins, 60)
                return (
                    False,
                    f"{self._daily_loss_reason()}. Новый день UTC через {h}ч {m}м",
                )
            if self.status != GuardStatus.STOPPED:
                self._trigger_stop(self._daily_loss_reason(), StopKind.DAILY_LOSS)

        if self.stop_kind == StopKind.DAILY_LOSS and self.daily_loss_blocks_until_next_day:
            mins = self._minutes_until_utc_reset()
            h, m = divmod(mins, 60)
            return (
                False,
                f"{self.stop_reason or self._daily_loss_reason()}. "
                f"Сброс в 00:00 UTC через {h}ч {m}м",
            )

        if self.status == GuardStatus.STOPPED and self.auto_stop_time:
            if self.stop_kind in (StopKind.DAILY_LOSS, StopKind.TRADE_LIMIT):
                if self.daily_loss_blocks_until_next_day and self.stop_kind == StopKind.DAILY_LOSS:
                    mins = self._minutes_until_utc_reset()
                    h, m = divmod(mins, 60)
                    return False, f"Дневной стоп. Сброс в 00:00 UTC через {h}ч {m}м"
                if self.stop_kind == StopKind.TRADE_LIMIT and self.day_stats.date == self._today_utc():
                    mins = self._minutes_until_utc_reset()
                    h, m = divmod(mins, 60)
                    return False, f"Лимит сделок на сегодня. Сброс через {h}ч {m}м"

            elapsed = (datetime.now(timezone.utc) - self.auto_stop_time).total_seconds()
            pause_sec = self.cooldown_after_stop_hours * 3600
            if elapsed < pause_sec:
                left = int((pause_sec - elapsed) / 60)
                return False, f"Пауза после стопа, осталось {left} мин"
            self.status = GuardStatus.ACTIVE
            self.stop_reason = ""
            self.stop_kind = StopKind.NONE
            self.auto_stop_time = None
            self._consecutive_losses = 0

        if self.last_loss_time:
            elapsed = (datetime.now(timezone.utc) - self.last_loss_time).total_seconds()
            if elapsed < self.cooldown_after_loss_sec:
                return False, f"Кулдаун после убытка: {int(self.cooldown_after_loss_sec - elapsed)} сек"

        if self.open_positions_count >= self.max_positions:
            return False, f"Макс. позиций: {self.max_positions}"

        if self.max_trades_per_day > 0 and self.day_stats.trades >= self.max_trades_per_day:
            return False, f"Лимит сделок на сегодня: {self.day_stats.trades}/{self.max_trades_per_day}"

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
        blocked = self.status != GuardStatus.ACTIVE or self._daily_loss_exceeded()
        reason = self.stop_reason if blocked else ""
        if blocked and self._daily_loss_exceeded() and not reason:
            reason = self._daily_loss_reason()
        return {
            "pnl_today_usdt": round(s.net_pnl_usdt, 2),
            "pnl_today_pct": round(s.net_pnl_pct, 2),
            "trades_today": s.trades,
            "consecutive_losses": self._consecutive_losses,
            "open_positions": self.open_positions_count,
            "max_positions": self.max_positions,
            "blocked": blocked,
            "block_reason": reason,
            "status": self.status.value,
            "reset_utc_in_min": self._minutes_until_utc_reset(),
        }
