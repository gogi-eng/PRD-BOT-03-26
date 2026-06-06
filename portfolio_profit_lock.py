#!/usr/bin/env python3
"""
PORTFOLIO PROFIT LOCK v1.0
Защита общей прибыли портфеля от разворота.

Логика:
1. Отслеживает суммарный PnL всех открытых позиций: a+b+c
2. При достижении 5% от депозита — активирует защиту
3. Запоминает пик прибыли (max_profit)
4. Если прибыль снижается на 20% от пика в течение 5 минут — закрывает ВСЕ позиции
5. Если прибыль обновляет максимум — таймер сбрасывается
6. Отправляет уведомление в Telegram при закрытии
"""
from __future__ import annotations
import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass
from enum import Enum

if TYPE_CHECKING:
    from exchange.bybit_client import BybitClient
    from tg.controller import TelegramController


class LockStatus(Enum):
    INACTIVE = "inactive"
    ARMED = "armed"
    DECLINING = "declining"
    TRIGGERED = "triggered"
    COOLDOWN = "cooldown"


@dataclass
class LockState:
    status: LockStatus = LockStatus.INACTIVE
    max_profit_usdt: float = 0.0
    max_profit_pct: float = 0.0
    current_profit_usdt: float = 0.0
    current_profit_pct: float = 0.0
    decline_start_time: Optional[float] = None
    decline_duration_sec: float = 0.0
    last_close_time: Optional[datetime] = None
    total_closed_pnl: float = 0.0
    positions_closed: int = 0


class PortfolioProfitLock:
    """
    Менеджер защиты прибыли портфеля.

    Параметры:
        min_profit_pct: 5% от депозита для активации
        decline_threshold_pct: -20% от пика для запуска таймера
        decline_duration_sec: 300с (5 минут) непрерывного снижения
        cooldown_sec: 3600с (1 час) пауза после закрытия
    """

    def __init__(
        self,
        client: "BybitClient",
        tg: "TelegramController" = None,
        min_profit_pct: float = 5.0,
        decline_threshold_pct: float = 20.0,
        decline_duration_sec: float = 300.0,
        cooldown_sec: float = 3600.0,
        dry_run: bool = False,
    ):
        self.client = client
        self.tg = tg
        self.min_profit_pct = min_profit_pct
        self.decline_threshold_pct = decline_threshold_pct
        self.decline_duration_sec = decline_duration_sec
        self.cooldown_sec = cooldown_sec
        self.dry_run = dry_run

        self.state = LockState()
        self._initial_balance: float = 0.0
        self._lock = asyncio.Lock()

        self._total_activations: int = 0
        self._total_closures: int = 0
        self._total_protected_pnl: float = 0.0

        print(f"[PROFIT_LOCK] Инициализирован: мин.прибыль={min_profit_pct}%, "
              f"порог снижения={decline_threshold_pct}%, таймер={decline_duration_sec}с")

    def set_initial_balance(self, balance: float):
        self._initial_balance = balance

    async def _get_position_pnl(self, symbol: str, position) -> float:
        """Рассчитывает PnL позиции по текущей цене."""
        try:
            entry = position.entry_price
            qty = position.qty
            if entry <= 0 or qty <= 0:
                return 0.0

            current_price = await self.client.get_price(symbol)
            if current_price <= 0:
                return 0.0

            if position.is_long:
                return (current_price - entry) * qty
            else:
                return (entry - current_price) * qty
        except Exception as e:
            print(f"[PROFIT_LOCK] Ошибка PnL {symbol}: {e}")
            return 0.0

    async def _calculate_portfolio_pnl(self, positions: dict) -> Tuple[float, float]:
        """Суммарный PnL всех позиций."""
        total_pnl = 0.0
        for symbol, pos in positions.items():
            pnl = await self._get_position_pnl(symbol, pos)
            total_pnl += pnl

        if self._initial_balance > 0:
            total_pnl_pct = (total_pnl / self._initial_balance) * 100
        else:
            total_pnl_pct = 0.0

        return total_pnl, total_pnl_pct

    async def _close_all_positions(self, positions: dict) -> Tuple[int, float]:
        """Закрывает ВСЕ открытые позиции одновременно."""
        closed_count = 0
        total_pnl = 0.0
        close_results = []

        print(f"[PROFIT_LOCK] ЗАКРЫТИЕ ВСЕХ ПОЗИЦИЙ ({len(positions)} шт)...")

        for symbol, pos in positions.items():
            try:
                if pos.qty <= 0:
                    continue
                pnl = await self._get_position_pnl(symbol, pos)

                if self.dry_run:
                    print(f"   [DRY RUN] Close {symbol} qty={pos.qty}")
                    closed_count += 1
                    total_pnl += pnl
                    close_results.append((symbol, pnl, True))
                else:
                    result = await self.client.close_position(symbol, pos.side, pos.qty)
                    if result.get("success"):
                        closed_count += 1
                        # Пробуем получить реальный PnL с биржи
                        try:
                            closed = await self.client.get_closed_pnl(symbol, limit=1)
                            if closed:
                                pnl = float(closed[0].get("closedPnl", pnl))
                        except Exception:
                            pass
                        total_pnl += pnl
                        close_results.append((symbol, pnl, True))
                        print(f"   Закрыто {symbol}: PnL ${pnl:+.2f}")
                    else:
                        error = result.get("error", "Unknown")
                        close_results.append((symbol, 0, False))
                        print(f"   Ошибка {symbol}: {error}")
            except Exception as e:
                print(f"   Исключение {symbol}: {e}")
                close_results.append((symbol, 0, False))

        await self._send_closure_notification(close_results, total_pnl)
        return closed_count, total_pnl

    async def _send_closure_notification(self, results: list, total_pnl: float):
        if not self.tg:
            return
        lines = [
            "<b>PORTFOLIO PROFIT LOCK СРАБОТАЛ!</b>",
            "",
            f"<b>Закрыто позиций:</b> {len(results)}",
            f"<b>Общий PnL:</b> <code>${total_pnl:+.2f}</code>",
            "",
            "<b>Детали:</b>",
        ]
        for symbol, pnl, success in results:
            status = "OK" if success else "FAIL"
            lines.append(f"  {status} {symbol}: <code>${pnl:+.2f}</code>")

        lines.extend([
            "",
            "<b>Статистика защиты:</b>",
            f"  Активаций: {self._total_activations}",
            f"  Закрытий: {self._total_closures}",
            f"  Защищено: <code>${self._total_protected_pnl:+.2f}</code>",
            "",
            f"Cooldown: {self.cooldown_sec / 3600:.1f}ч",
        ])

        try:
            await self.tg.send_message("\n".join(lines))
        except Exception as e:
            print(f"[PROFIT_LOCK] Ошибка TG: {e}")

    async def check(self, positions: dict) -> Optional[List[str]]:
        """
        Главная функция проверки. Вызывать каждый цикл!

        Args:
            positions: dict {symbol: Position} из PositionManager

        Returns:
            Список закрытых символов или None
        """
        async with self._lock:
            if not positions:
                if self.state.status not in (LockStatus.INACTIVE, LockStatus.COOLDOWN):
                    self.state = LockState()
                return None

            # Cooldown
            if self.state.status == LockStatus.COOLDOWN:
                if self.state.last_close_time:
                    elapsed = (datetime.now(timezone.utc) - self.state.last_close_time).total_seconds()
                    if elapsed < self.cooldown_sec:
                        return None
                    else:
                        self.state.status = LockStatus.INACTIVE
                        self.state.last_close_time = None

            # PnL портфеля
            current_pnl, current_pnl_pct = await self._calculate_portfolio_pnl(positions)
            self.state.current_profit_usdt = current_pnl
            self.state.current_profit_pct = current_pnl_pct

            # === INACTIVE: ждём активации ===
            if self.state.status == LockStatus.INACTIVE:
                if current_pnl_pct >= self.min_profit_pct:
                    self.state.status = LockStatus.ARMED
                    self.state.max_profit_usdt = current_pnl
                    self.state.max_profit_pct = current_pnl_pct
                    self._total_activations += 1
                    print(f"[PROFIT_LOCK] ЗАЩИТА АКТИВИРОВАНА: PnL ${current_pnl:+.2f} ({current_pnl_pct:+.1f}%)")
                return None

            # === ARMED: отслеживаем пик ===
            if self.state.status == LockStatus.ARMED:
                if current_pnl > self.state.max_profit_usdt:
                    self.state.max_profit_usdt = current_pnl
                    self.state.max_profit_pct = current_pnl_pct
                    print(f"[PROFIT_LOCK] Новый пик: ${current_pnl:+.2f} ({current_pnl_pct:+.1f}%)")

                if self.state.max_profit_usdt > 0:
                    decline_pct = ((self.state.max_profit_usdt - current_pnl) / self.state.max_profit_usdt) * 100
                    if decline_pct >= self.decline_threshold_pct:
                        self.state.status = LockStatus.DECLINING
                        self.state.decline_start_time = time.time()
                        print(f"[PROFIT_LOCK] Снижение {decline_pct:.1f}% от пика — запуск таймера")
                return None

            # === DECLINING: таймер 5 минут ===
            if self.state.status == LockStatus.DECLINING:
                # Новый пик — сброс
                if current_pnl > self.state.max_profit_usdt:
                    self.state.max_profit_usdt = current_pnl
                    self.state.max_profit_pct = current_pnl_pct
                    self.state.status = LockStatus.ARMED
                    self.state.decline_start_time = None
                    self.state.decline_duration_sec = 0
                    print("[PROFIT_LOCK] Прибыль обновилась — сброс таймера")
                    return None

                if self.state.max_profit_usdt > 0:
                    decline_pct = ((self.state.max_profit_usdt - current_pnl) / self.state.max_profit_usdt) * 100

                    # Снижение ниже порога — возврат в ARMED
                    if decline_pct < self.decline_threshold_pct:
                        self.state.status = LockStatus.ARMED
                        self.state.decline_start_time = None
                        self.state.decline_duration_sec = 0
                        print(f"[PROFIT_LOCK] Снижение < {self.decline_threshold_pct}% — сброс")
                        return None

                    # Считаем время
                    if self.state.decline_start_time:
                        self.state.decline_duration_sec = time.time() - self.state.decline_start_time
                        print(f"[PROFIT_LOCK] Снижение {decline_pct:.1f}%: "
                              f"{self.state.decline_duration_sec:.0f}с / {self.decline_duration_sec:.0f}с")

                        # ТАЙМЕР ИСТЁК — ЗАКРЫВАЕМ ВСЁ
                        if self.state.decline_duration_sec >= self.decline_duration_sec:
                            print("[PROFIT_LOCK] ТАЙМЕР ИСТЁК! Закрываем все позиции...")
                            closed_symbols = list(positions.keys())
                            closed_count, total_pnl = await self._close_all_positions(positions)

                            self._total_closures += 1
                            self._total_protected_pnl += total_pnl
                            self.state.status = LockStatus.COOLDOWN
                            self.state.last_close_time = datetime.now(timezone.utc)
                            self.state.positions_closed = closed_count
                            self.state.total_closed_pnl = total_pnl

                            print(f"[PROFIT_LOCK] Закрыто {closed_count} позиций, PnL: ${total_pnl:+.2f}")
                            return closed_symbols

            return None

    def get_status(self) -> Dict:
        cooldown_remaining = 0.0
        if self.state.status == LockStatus.COOLDOWN and self.state.last_close_time:
            elapsed = (datetime.now(timezone.utc) - self.state.last_close_time).total_seconds()
            cooldown_remaining = max(0, self.cooldown_sec - elapsed)

        return {
            "status": self.state.status.value,
            "current_profit_usdt": round(self.state.current_profit_usdt, 2),
            "current_profit_pct": round(self.state.current_profit_pct, 2),
            "max_profit_usdt": round(self.state.max_profit_usdt, 2),
            "max_profit_pct": round(self.state.max_profit_pct, 2),
            "decline_duration_sec": round(self.state.decline_duration_sec, 1),
            "cooldown_remaining_sec": round(cooldown_remaining, 0),
            "total_activations": self._total_activations,
            "total_closures": self._total_closures,
            "total_protected_pnl": round(self._total_protected_pnl, 2),
        }

    def get_report(self) -> str:
        s = self.get_status()
        status_emoji = {
            "inactive": "ВЫКЛ", "armed": "АКТИВНА",
            "declining": "СНИЖЕНИЕ", "triggered": "СРАБОТАЛА", "cooldown": "ПАУЗА",
        }
        lines = [
            "<b>PORTFOLIO PROFIT LOCK</b>",
            "",
            f"<b>Статус:</b> <code>{status_emoji.get(s['status'], s['status'])}</code>",
            f"<b>Текущая прибыль:</b> <code>${s['current_profit_usdt']:+.2f}</code> ({s['current_profit_pct']:+.1f}%)",
            f"<b>Пик прибыли:</b> <code>${s['max_profit_usdt']:+.2f}</code> ({s['max_profit_pct']:+.1f}%)",
        ]
        if s["status"] == "declining":
            lines.append(f"<b>Таймер:</b> {s['decline_duration_sec']:.0f}с / {self.decline_duration_sec:.0f}с")
        if s["cooldown_remaining_sec"] > 0:
            lines.append(f"<b>Cooldown:</b> {s['cooldown_remaining_sec'] / 60:.0f}мин")
        lines.extend([
            "",
            "<b>Статистика:</b>",
            f"  Активаций: {s['total_activations']}",
            f"  Закрытий: {s['total_closures']}",
            f"  Защищено: <code>${s['total_protected_pnl']:+.2f}</code>",
        ])
        return "\n".join(lines)

    def reset(self):
        self.state = LockState()
        print("[PROFIT_LOCK] Сброс состояния")
