#!/usr/bin/env python3
"""
RISK MANAGER — ЕДИНСТВЕННЫЙ модуль управления рисками.

Заменяет: core/risk_manager, core/smart_risk, core/position_sizer,
           risk_guard, core/guard, emergent_v3/guard_v3
"""
from __future__ import annotations
import threading
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field
from datetime import datetime, date, timezone, timedelta
from enum import Enum
import asyncio
import inspect


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
    max_consecutive_losses: int = 0


class RiskGuard:
    """
    Единственный риск-менеджер бота.

    Функции:
    1. Position sizing (на основе ATR)
    2. Daily loss limit
    3. Consecutive loss tracking
    4. Max positions
    5. Cooldowns
    6. Emergency stop
    """

    def __init__(
        self,
        max_consecutive_losses: int = 4,
        max_daily_loss_pct: float = 5.0,
        max_daily_loss_usdt: float = 100.0,
        max_trades_per_day: int = 20,
        max_positions: int = 3,
        max_trades_per_symbol_24h: int = 8,
        cooldown_after_loss_sec: int = 300,
        cooldown_after_stop_hours: int = 2,
        initial_balance: float = 0.0,
        reduce_after_losses: int = 2,
        reduction_factor: float = 0.5,
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_daily_loss_usdt = max_daily_loss_usdt
        self.max_trades_per_day = max_trades_per_day
        self.max_positions = max_positions
        self.max_trades_per_symbol_24h = max_trades_per_symbol_24h
        self.cooldown_after_loss_sec = cooldown_after_loss_sec
        self.cooldown_after_stop_hours = cooldown_after_stop_hours
        self.initial_balance = initial_balance
        self.reduce_after_losses = reduce_after_losses
        self.reduction_factor = reduction_factor

        self.status: GuardStatus = GuardStatus.ACTIVE
        self.stop_reason: str = ""
        self.day_stats = DayStats(date=date.today())
        self.last_loss_time: Optional[datetime] = None
        self.auto_stop_time: Optional[datetime] = None
        self._consecutive_losses: int = 0
        self._notify_callback: Optional[Callable] = None
        self._lock = threading.Lock()
        self._symbol_trade_times: Dict[str, List[datetime]] = {}
        self._symbol_last_loss: Dict[str, datetime] = {}

    def set_notify_callback(self, callback: Callable):
        self._notify_callback = callback

    # === Day management ===

    def _ensure_today(self):
        today = date.today()
        if self.day_stats.date != today:
            self.day_stats = DayStats(date=today)
            self._consecutive_losses = 0
            self.status = GuardStatus.ACTIVE
            self.stop_reason = ""
            self.last_loss_time = None
            self.auto_stop_time = None
            self._symbol_last_loss.clear()
            print(f"[RISK] New day: {today}")

    # === Trade recording ===

    def record_trade(self, pnl: float, symbol: str = None):
        """Записать результат сделки."""
        with self._lock:
            self._ensure_today()
            stats = self.day_stats
            stats.trades += 1
            stats.net_pnl_usdt += pnl

            if self.initial_balance > 0:
                stats.net_pnl_pct = (stats.net_pnl_usdt / self.initial_balance) * 100

            if pnl > 0:
                stats.wins += 1
                self._consecutive_losses = 0
            else:
                stats.losses += 1
                self._consecutive_losses += 1
                stats.consecutive_losses = self._consecutive_losses
                stats.max_consecutive_losses = max(stats.max_consecutive_losses, self._consecutive_losses)
                now = datetime.now(timezone.utc)
                self.last_loss_time = now
                if symbol:
                    self._symbol_last_loss[symbol] = now

            if symbol and self.max_trades_per_symbol_24h > 0:
                self._symbol_trade_times.setdefault(symbol, []).append(datetime.now(timezone.utc))

            self._check_auto_stop()

    def _check_auto_stop(self):
        # Consecutive losses
        if self._consecutive_losses >= self.max_consecutive_losses:
            self._trigger_stop(f"{self._consecutive_losses} consecutive losses")
            return
        # Daily loss limit %
        if self.max_daily_loss_pct > 0 and self.day_stats.net_pnl_pct <= -self.max_daily_loss_pct:
            self._trigger_stop(f"Daily loss {self.day_stats.net_pnl_pct:.1f}%")
            return
        # Daily loss limit USDT
        if self.max_daily_loss_usdt > 0 and self.day_stats.net_pnl_usdt <= -self.max_daily_loss_usdt:
            self._trigger_stop(f"Daily loss ${self.day_stats.net_pnl_usdt:.2f}")
            return
        # Max trades
        if self.max_trades_per_day > 0 and self.day_stats.trades >= self.max_trades_per_day:
            self._trigger_stop(f"Max trades: {self.day_stats.trades}")

    def _trigger_stop(self, reason: str):
        if self.status not in [GuardStatus.STOPPED, GuardStatus.EMERGENCY]:
            self.status = GuardStatus.STOPPED
            self.stop_reason = reason
            self.auto_stop_time = datetime.now(timezone.utc)
            print(f"[RISK] AUTO-STOP: {reason}")
            self._notify(f"AUTO-STOP: {reason}")

    def _notify(self, message: str):
        if self._notify_callback:
            try:
                if asyncio.iscoroutinefunction(self._notify_callback):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._notify_callback(message))
                    except RuntimeError:
                        pass
                else:
                    result = self._notify_callback(message)
                    if inspect.iscoroutine(result):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(result)
                        except RuntimeError:
                            pass
            except Exception as e:
                print(f"[RISK] Notify error: {e}")

    # === Trade permission ===

    def can_trade(self, symbol: str = None) -> Tuple[bool, str]:
        """Проверить, можно ли торговать."""
        with self._lock:
            self._ensure_today()

            if self.status == GuardStatus.EMERGENCY:
                return False, f"EMERGENCY: {self.stop_reason}"

            if self.status == GuardStatus.STOPPED:
                if self.auto_stop_time:
                    elapsed = (datetime.now(timezone.utc) - self.auto_stop_time).total_seconds()
                    remaining = self.cooldown_after_stop_hours * 3600 - elapsed
                    if remaining > 0:
                        return False, f"Auto-stop, resume in {int(remaining/60)}m"
                    else:
                        self._consecutive_losses = 0
                        self.status = GuardStatus.ACTIVE
                        self.stop_reason = ""
                        self.auto_stop_time = None
                        print("[RISK] Cooldown passed, resuming")
                else:
                    return False, f"STOPPED: {self.stop_reason}"

            # Cooldown after loss
            if symbol and symbol in self._symbol_last_loss:
                elapsed = (datetime.now(timezone.utc) - self._symbol_last_loss[symbol]).total_seconds()
                if elapsed < self.cooldown_after_loss_sec:
                    return False, f"Cooldown {symbol}: {int(self.cooldown_after_loss_sec - elapsed)}s"
            elif self.last_loss_time:
                elapsed = (datetime.now(timezone.utc) - self.last_loss_time).total_seconds()
                if elapsed < self.cooldown_after_loss_sec:
                    return False, f"Cooldown: {int(self.cooldown_after_loss_sec - elapsed)}s"

            # Limits check
            if self._consecutive_losses >= self.max_consecutive_losses:
                return False, f"{self._consecutive_losses} consecutive losses"
            if self.max_trades_per_day > 0 and self.day_stats.trades >= self.max_trades_per_day:
                return False, f"Max trades: {self.day_stats.trades}"

            # Per-symbol check
            if symbol and self.max_trades_per_symbol_24h > 0:
                count = self._count_symbol_trades(symbol)
                if count >= self.max_trades_per_symbol_24h:
                    return False, f"Max trades for {symbol}: {count}"

            return True, ""

    def _count_symbol_trades(self, symbol: str) -> int:
        if symbol not in self._symbol_trade_times:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        times = [t for t in self._symbol_trade_times[symbol] if t >= cutoff]
        self._symbol_trade_times[symbol] = times
        return len(times)

    # === Position sizing ===

    def calculate_position_size(self, balance: float, risk_pct: float, entry: float,
                                stop_loss: float, leverage: int, atr_value: float = 0) -> float:
        """
        Рассчитать размер позиции.

        Формула: size = (balance * risk%) / (distance_to_SL * leverage_factor)
        """
        if entry <= 0 or stop_loss <= 0 or balance <= 0:
            return 0.0

        risk_amount = balance * (risk_pct / 100)
        distance = abs(entry - stop_loss)
        if distance <= 0:
            return 0.0

        # Size multiplier based on losses
        multiplier = self.get_size_multiplier()

        qty = (risk_amount * multiplier) / distance
        return qty

    def get_size_multiplier(self) -> float:
        """Мультипликатор размера позиции."""
        mult = 1.0
        if self._consecutive_losses >= self.reduce_after_losses:
            mult *= self.reduction_factor
        if self.initial_balance > 0 and self.day_stats.net_pnl_pct <= -(self.max_daily_loss_pct * 0.5):
            mult *= 0.75
        return max(0.1, min(mult, 1.5))

    # === Control ===

    def reset_guard(self):
        with self._lock:
            if self.status == GuardStatus.STOPPED:
                self.status = GuardStatus.ACTIVE
            self.stop_reason = ""
            self._consecutive_losses = 0
            self.last_loss_time = None
            self.auto_stop_time = None
            print("[RISK] Guard reset")

    def resume(self):
        with self._lock:
            if self.status in [GuardStatus.STOPPED]:
                self.status = GuardStatus.ACTIVE
                self.stop_reason = ""
                self._consecutive_losses = 0
                self.auto_stop_time = None
                self.last_loss_time = None
                print("[RISK] Resumed")

    def emergency_stop(self, reason: str = "Manual"):
        with self._lock:
            self.status = GuardStatus.EMERGENCY
            self.stop_reason = reason
            print(f"[RISK] EMERGENCY STOP: {reason}")
            self._notify(f"EMERGENCY STOP: {reason}")

    def snapshot(self) -> Dict:
        self._ensure_today()
        s = self.day_stats
        blocked = self.status in (GuardStatus.STOPPED, GuardStatus.EMERGENCY)
        return {
            "date": str(s.date),
            "pnl_today": round(s.net_pnl_usdt, 2),
            "pnl_today_pct": round(s.net_pnl_pct, 1),
            "trades_today": s.trades,
            "wins_today": s.wins,
            "losses_today": s.losses,
            "consecutive_losses": self._consecutive_losses,
            "blocked": blocked,
            "block_reason": self.stop_reason if blocked else "",
            "status": self.status.value,
            "size_multiplier": self.get_size_multiplier(),
        }
