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
        min_loss_usdt_for_cooldown: float = 0.0,
        min_loss_usdt_for_consecutive: float = 0.0,
        ignore_loss_cooldown_reasons: Optional[List[str]] = None,
        ignore_consecutive_loss_reasons: Optional[List[str]] = None,
        symbol_loss_streak_cooldown_enabled: bool = False,
        symbol_loss_streak_threshold: int = 0,
        symbol_loss_streak_limit: Optional[int] = None,
        symbol_loss_streak_cooldown_count: Optional[int] = None,
        symbol_loss_streak_cooldown_sec: int = 0,
        trend_exit_reentry_cooldown_enabled: bool = False,
        trend_exit_reentry_cooldown_sec: int = 0,
        trend_exit_reentry_loss_only: bool = True,
        early_exit_reentry_cooldown_enabled: bool = False,
        early_exit_reentry_cooldown_sec: int = 0,
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
        self.min_loss_usdt_for_cooldown = max(float(min_loss_usdt_for_cooldown), 0.0)
        self.min_loss_usdt_for_consecutive = max(float(min_loss_usdt_for_consecutive), 0.0)
        self.ignore_loss_cooldown_reasons = {
            str(value).strip().lower() for value in (ignore_loss_cooldown_reasons or []) if str(value).strip()
        }
        self.ignore_consecutive_loss_reasons = {
            str(value).strip().lower() for value in (ignore_consecutive_loss_reasons or []) if str(value).strip()
        }
        resolved_streak_threshold = int(symbol_loss_streak_threshold)
        if symbol_loss_streak_cooldown_count is not None:
            resolved_streak_threshold = int(symbol_loss_streak_cooldown_count)
        if symbol_loss_streak_limit is not None:
            resolved_streak_threshold = int(symbol_loss_streak_limit)
        self.symbol_loss_streak_cooldown_enabled = bool(symbol_loss_streak_cooldown_enabled)
        self.symbol_loss_streak_threshold = max(int(resolved_streak_threshold), 0)
        # Backward-compatible alias used by older tests/configs.
        self.symbol_loss_streak_cooldown_count = self.symbol_loss_streak_threshold
        self.symbol_loss_streak_cooldown_sec = max(int(symbol_loss_streak_cooldown_sec), 0)
        self.trend_exit_reentry_cooldown_enabled = bool(trend_exit_reentry_cooldown_enabled)
        self.trend_exit_reentry_cooldown_sec = max(int(trend_exit_reentry_cooldown_sec), 0)
        self.trend_exit_reentry_loss_only = bool(trend_exit_reentry_loss_only)
        self.early_exit_reentry_cooldown_enabled = bool(early_exit_reentry_cooldown_enabled)
        self.early_exit_reentry_cooldown_sec = max(int(early_exit_reentry_cooldown_sec), 0)

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
        self._symbol_consecutive_losses: Dict[str, int] = {}
        self._symbol_streak_cooldown_until: Dict[str, datetime] = {}
        self._symbol_trend_exit_cooldown_until: Dict[str, datetime] = {}
        self._symbol_early_exit_cooldown_until: Dict[str, datetime] = {}

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
            self._symbol_consecutive_losses.clear()
            self._symbol_streak_cooldown_until.clear()
            # Keep trend-exit cooldowns across day boundary (12h window may overlap midnight).
            now = datetime.now(timezone.utc)
            self._symbol_trend_exit_cooldown_until = {
                s: ts for s, ts in self._symbol_trend_exit_cooldown_until.items() if ts > now
            }
            # Keep early-exit cooldowns across day boundary as well.
            self._symbol_early_exit_cooldown_until = {
                s: ts for s, ts in self._symbol_early_exit_cooldown_until.items() if ts > now
            }
            print(f"[RISK] New day: {today}")

    # === Trade recording ===

    @staticmethod
    def _reason_is_ignored(reason_key: str, ignored_reasons: set[str]) -> bool:
        """Treat base ignore reasons as prefix matches for sub-reasons.

        Example:
        - ignore: exchange_closed
        - reason: exchange_closed_tp_hit / exchange_closed_sl_hit / exchange_closed:...
        """
        if not reason_key:
            return False
        if reason_key in ignored_reasons:
            return True
        for base_reason in ignored_reasons:
            if reason_key.startswith(f"{base_reason}_") or reason_key.startswith(f"{base_reason}:"):
                return True
        return False

    def record_trade(self, pnl: float, symbol: str = None, reason: str = ""):
        """Записать результат сделки."""
        with self._lock:
            self._ensure_today()
            stats = self.day_stats
            stats.trades += 1
            stats.net_pnl_usdt += pnl
            reason_key = str(reason or "").strip().lower()

            if self.initial_balance > 0:
                stats.net_pnl_pct = (stats.net_pnl_usdt / self.initial_balance) * 100

            if pnl > 0:
                stats.wins += 1
                self._consecutive_losses = 0
                if symbol:
                    self._symbol_consecutive_losses[symbol] = 0
            else:
                stats.losses += 1
                abs_loss = abs(float(pnl))
                count_for_consecutive = (
                    abs_loss > self.min_loss_usdt_for_consecutive
                    and not self._reason_is_ignored(reason_key, self.ignore_consecutive_loss_reasons)
                )
                apply_cooldown = (
                    abs_loss > self.min_loss_usdt_for_cooldown
                    and not self._reason_is_ignored(reason_key, self.ignore_loss_cooldown_reasons)
                )

                if count_for_consecutive:
                    self._consecutive_losses += 1
                    stats.consecutive_losses = self._consecutive_losses
                    stats.max_consecutive_losses = max(stats.max_consecutive_losses, self._consecutive_losses)

                if apply_cooldown:
                    now = datetime.now(timezone.utc)
                    self.last_loss_time = now
                    if symbol:
                        self._symbol_last_loss[symbol] = now
                if symbol and count_for_consecutive:
                    symbol_losses = int(self._symbol_consecutive_losses.get(symbol, 0)) + 1
                    self._symbol_consecutive_losses[symbol] = symbol_losses
                    if (
                        self.symbol_loss_streak_cooldown_enabled
                        and self.symbol_loss_streak_threshold > 0
                        and self.symbol_loss_streak_cooldown_sec > 0
                        and symbol_losses >= self.symbol_loss_streak_threshold
                    ):
                        self._symbol_streak_cooldown_until[symbol] = (
                            datetime.now(timezone.utc) + timedelta(seconds=self.symbol_loss_streak_cooldown_sec)
                        )
                if (
                    symbol
                    and self.trend_exit_reentry_cooldown_enabled
                    and self.trend_exit_reentry_cooldown_sec > 0
                    and str(reason_key).startswith("trend_exit")
                    and ((not self.trend_exit_reentry_loss_only) or pnl < 0)
                ):
                    self._symbol_trend_exit_cooldown_until[symbol] = (
                        datetime.now(timezone.utc) + timedelta(seconds=self.trend_exit_reentry_cooldown_sec)
                    )
                if (
                    symbol
                    and self.early_exit_reentry_cooldown_enabled
                    and self.early_exit_reentry_cooldown_sec > 0
                    and str(reason_key).startswith("early_exit")
                ):
                    self._symbol_early_exit_cooldown_until[symbol] = (
                        datetime.now(timezone.utc) + timedelta(seconds=self.early_exit_reentry_cooldown_sec)
                    )
            if pnl > 0 and symbol:
                self._symbol_streak_cooldown_until.pop(symbol, None)

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
            if self.symbol_loss_streak_cooldown_enabled and symbol and symbol in self._symbol_streak_cooldown_until:
                remain = (self._symbol_streak_cooldown_until[symbol] - datetime.now(timezone.utc)).total_seconds()
                if remain > 0:
                    return False, f"Symbol streak cooldown {symbol}: {int(remain)}s"
                self._symbol_streak_cooldown_until.pop(symbol, None)
            if symbol and symbol in self._symbol_trend_exit_cooldown_until:
                remain = (
                    self._symbol_trend_exit_cooldown_until[symbol] - datetime.now(timezone.utc)
                ).total_seconds()
                if remain > 0:
                    return False, f"Trend-exit cooldown {symbol}: {int(remain)}s"
                self._symbol_trend_exit_cooldown_until.pop(symbol, None)
            if symbol and symbol in self._symbol_early_exit_cooldown_until:
                remain = (
                    self._symbol_early_exit_cooldown_until[symbol] - datetime.now(timezone.utc)
                ).total_seconds()
                if remain > 0:
                    return False, f"Early-exit cooldown {symbol}: {int(remain)}s"
                self._symbol_early_exit_cooldown_until.pop(symbol, None)

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

    def calculate_position_size(
        self,
        balance: float,
        risk_pct: float,
        entry: float,
        stop_loss: float,
        leverage: int,
        atr_value: float = 0,
        capital_weight: float = 1.0,
        margin_cap_pct: float = 10.0,
        size_mode: str = "risk",
        mode: str | None = None,
    ) -> float:
        """
        Рассчитать размер позиции с ЖЁСТКИМИ лимитами.

        Modes:
        - risk: qty по риску к SL, с верхним лимитом по марже.
        - margin_cap: qty целится в лимит по марже (ожидаемый notional = margin_pct% * balance * leverage).

        1. Risk-based: qty = (balance * risk%) / distance_to_SL
        2. Margin cap: notional не может превышать margin_pct% от баланса * leverage
        3. Multiplier: уменьшение после серии убытков
        """
        if entry <= 0 or stop_loss <= 0 or balance <= 0:
            return 0.0

        capital_weight = max(0.2, min(capital_weight, 1.5))
        risk_amount = balance * (risk_pct / 100) * capital_weight
        distance = abs(entry - stop_loss)
        if distance <= 0:
            return 0.0

        # Size multiplier based on losses
        multiplier = self.get_size_multiplier()

        # 1. Risk-based qty
        qty_risk = (risk_amount * multiplier) / distance

        # 2. ЖЁСТКИЙ ЛИМИТ: маржа не более margin_cap_pct% от баланса
        # capital_weight is also applied for predictable top-N allocation.
        max_margin = balance * (margin_cap_pct / 100) * capital_weight
        max_notional = max_margin * leverage
        max_qty_by_margin = max_notional / entry if entry > 0 else 0

        mode = str(mode or size_mode or "risk").strip().lower()
        if mode == "margin_cap":
            qty = max_qty_by_margin
            print(
                f"[RISK] Margin-cap sizing: qty={qty:.4f} "
                f"(margin ${max_margin:.2f}, notional ${max_notional:.2f}, weight={capital_weight:.2f})"
            )
        else:
            qty = qty_risk

        if qty > max_qty_by_margin:
            print(f"[RISK] Position capped: qty {qty:.4f} -> {max_qty_by_margin:.4f} "
                  f"(margin ${max_margin:.2f}, notional ${max_notional:.2f})")
            qty = max_qty_by_margin

        # 3. Абсолютный лимит: notional не более 50% баланса * leverage
        absolute_max_notional = balance * 0.35 * leverage * max(capital_weight, 0.5)
        absolute_max_qty = absolute_max_notional / entry if entry > 0 else 0
        if qty > absolute_max_qty:
            print(f"[RISK] HARD CAP: qty {qty:.4f} -> {absolute_max_qty:.4f}")
            qty = absolute_max_qty

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
            if self.status in [GuardStatus.STOPPED, GuardStatus.EMERGENCY]:
                self.status = GuardStatus.ACTIVE
            self.stop_reason = ""
            self._consecutive_losses = 0
            self.last_loss_time = None
            self.auto_stop_time = None
            print("[RISK] Guard reset")

    def resume(self):
        with self._lock:
            if self.status in [GuardStatus.STOPPED, GuardStatus.EMERGENCY]:
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
