"""
PORTFOLIO PROFIT LOCK v1.1 (prd_agent/positions/portfolio_profit_lock.py)
Защита общей прибыли портфеля от разворота.

Логика:
1. Отслеживает суммарный PnL всех открытых позиций (с учётом skip_manual_positions).
2. При достижении min_profit_pct (по умолчанию 5%) от депозита — активирует защиту (ARMED).
3. Запоминает пик прибыли (max_profit_usdt).
4. Если прибыль снижается на decline_threshold_pct (20%) от пика в течение decline_duration_sec (300с) — закрывает позиции.
5. Если прибыль обновляет максимум — таймер сбрасывается.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from exchange.bybit_client import BybitClient

logger = logging.getLogger("prd_agent.positions.profit_lock")


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


@dataclass
class _PositionAdapter:
    symbol: str
    side: str
    qty: float
    entry_price: float
    origin: str = "bot"
    unrealised_pnl: Optional[float] = None

    @property
    def is_long(self) -> bool:
        return str(self.side or "").lower() in ("buy", "long")


class PortfolioProfitLock:
    """Менеджер защиты прибыли портфеля."""

    def __init__(
        self,
        client: Any,
        tg: Any = None,
        min_profit_pct: float = 5.0,
        decline_threshold_pct: float = 20.0,
        decline_duration_sec: float = 300.0,
        cooldown_sec: float = 3600.0,
        dry_run: bool = False,
        skip_manual_positions: bool = True,
        enabled: bool = False,
    ):
        self.client = client
        self.tg = tg
        self.enabled = bool(enabled)
        self.min_profit_pct = float(min_profit_pct)
        self.decline_threshold_pct = float(decline_threshold_pct)
        self.decline_duration_sec = float(decline_duration_sec)
        self.cooldown_sec = float(cooldown_sec)
        self.dry_run = bool(dry_run)
        self.skip_manual_positions = bool(skip_manual_positions)

        self.state = LockState()
        self._initial_balance: float = 0.0
        self._bot_symbols: Set[str] = set()
        self._lock = asyncio.Lock()

        self._total_activations: int = 0
        self._total_closures: int = 0
        self._total_protected_pnl: float = 0.0

    @classmethod
    def from_cfg(cls, cfg: Dict[str, Any], client: Any, tg: Any = None) -> "PortfolioProfitLock":
        pl = cfg.get("profit_lock") if isinstance(cfg.get("profit_lock"), dict) else {}
        ptp = cfg.get("portfolio_tp") if isinstance(cfg.get("portfolio_tp"), dict) else {}
        skip_manual = bool(
            pl.get("skip_manual_positions", ptp.get("skip_manual_positions", True))
        )
        return cls(
            client=client,
            tg=tg,
            enabled=bool(pl.get("enabled", False)),
            min_profit_pct=float(pl.get("min_profit_pct", 5.0) or 5.0),
            decline_threshold_pct=float(pl.get("decline_threshold_pct", 20.0) or 20.0),
            decline_duration_sec=float(pl.get("decline_duration_sec", 300.0) or 300.0),
            cooldown_sec=float(pl.get("cooldown_sec", 3600.0) or 3600.0),
            dry_run=bool(pl.get("dry_run", False)),
            skip_manual_positions=skip_manual,
        )

    def apply_config(self, cfg: Dict[str, Any]) -> None:
        pl = cfg.get("profit_lock") if isinstance(cfg.get("profit_lock"), dict) else {}
        ptp = cfg.get("portfolio_tp") if isinstance(cfg.get("portfolio_tp"), dict) else {}
        self.enabled = bool(pl.get("enabled", False))
        self.min_profit_pct = float(pl.get("min_profit_pct", self.min_profit_pct) or 5.0)
        self.decline_threshold_pct = float(
            pl.get("decline_threshold_pct", self.decline_threshold_pct) or 20.0
        )
        self.decline_duration_sec = float(
            pl.get("decline_duration_sec", self.decline_duration_sec) or 300.0
        )
        self.cooldown_sec = float(pl.get("cooldown_sec", self.cooldown_sec) or 3600.0)
        self.dry_run = bool(pl.get("dry_run", self.dry_run))
        self.skip_manual_positions = bool(
            pl.get("skip_manual_positions", ptp.get("skip_manual_positions", True))
        )

    def set_initial_balance(self, balance: float) -> None:
        if float(balance or 0) > 0:
            self._initial_balance = float(balance)

    def set_bot_symbols(self, symbols: Iterable[str]) -> None:
        self._bot_symbols = {str(s).upper() for s in (symbols or []) if s}

    def _normalize_position(self, symbol: str, pos: Any) -> Optional[_PositionAdapter]:
        sym = str(symbol or "").upper()
        if isinstance(pos, dict):
            sym = str(pos.get("symbol", sym) or sym).upper()
            qty = 0.0
            for k in ("size", "qty", "positionQty"):
                val = float(pos.get(k, 0) or 0)
                if val > 0:
                    qty = val
                    break
            entry = float(pos.get("avgPrice") or pos.get("entryPrice") or pos.get("entry") or 0)
            side = str(pos.get("side", "Buy") or "Buy")
            origin = str(pos.get("origin") or ("bot" if sym in self._bot_symbols else "manual")).lower()
            upnl_raw = pos.get("unrealisedPnl")
            upnl = float(upnl_raw) if upnl_raw not in (None, "") else None
            if not sym or qty <= 0:
                return None
            return _PositionAdapter(
                symbol=sym,
                side=side,
                qty=qty,
                entry_price=entry,
                origin=origin,
                unrealised_pnl=upnl,
            )
        qty = float(getattr(pos, "qty", getattr(pos, "size", 0)) or 0)
        if not sym or qty <= 0:
            return None
        entry = float(getattr(pos, "entry_price", getattr(pos, "entry", 0)) or 0)
        side = str(getattr(pos, "side", "Buy" if getattr(pos, "is_long", True) else "Sell"))
        origin_attr = getattr(pos, "origin", None)
        if origin_attr is not None:
            origin = str(origin_attr).lower()
        elif self._bot_symbols:
            origin = "bot" if sym in self._bot_symbols else "manual"
        else:
            origin = "bot"
        return _PositionAdapter(
            symbol=sym,
            side=side,
            qty=qty,
            entry_price=entry,
            origin=origin,
        )

    def _filter_positions(
        self,
        positions: Union[Dict[str, Any], List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Фильтрует открытые позиции (пропускает пустые и ручные при skip_manual_positions=True)."""
        if not positions:
            return {}
        raw_items: List[Tuple[str, Any]] = []
        if isinstance(positions, dict):
            raw_items = list(positions.items())
        elif isinstance(positions, list):
            for row in positions:
                if isinstance(row, dict):
                    sym = str(row.get("symbol", "")).upper()
                    if sym:
                        raw_items.append((sym, row))

        filtered: Dict[str, Any] = {}
        for sym, pos in raw_items:
            norm = self._normalize_position(sym, pos)
            if norm is None or norm.qty <= 0:
                continue
            if self.skip_manual_positions and norm.origin == "manual":
                continue
            filtered[norm.symbol] = pos if hasattr(pos, "entry_price") and hasattr(pos, "qty") else norm
        return filtered

    async def _get_position_pnl(self, symbol: str, position: Any) -> float:
        """Рассчитывает PnL позиции по текущей цене."""
        try:
            if hasattr(position, "unrealised_pnl") and position.unrealised_pnl is not None:
                # Если get_price не нужен или недоступен, используем unrealisedPnl при fallback
                pass
            entry = float(getattr(position, "entry_price", 0) or 0)
            qty = float(getattr(position, "qty", 0) or 0)
            if entry <= 0 or qty <= 0:
                return float(getattr(position, "unrealised_pnl", 0.0) or 0.0)

            current_price = float(await self.client.get_price(symbol) or 0.0)
            if current_price <= 0:
                return float(getattr(position, "unrealised_pnl", 0.0) or 0.0)

            is_long = bool(getattr(position, "is_long", str(getattr(position, "side", "")).lower() in ("buy", "long")))
            if is_long:
                return (current_price - entry) * qty
            return (entry - current_price) * qty
        except Exception as e:
            logger.warning("PROFIT_LOCK PnL error %s: %s", symbol, e)
            return 0.0

    async def _calculate_portfolio_pnl(self, positions: dict) -> Tuple[float, float]:
        """Суммарный PnL всех отслеживаемых позиций."""
        total_pnl = 0.0
        for symbol, pos in positions.items():
            pnl = await self._get_position_pnl(symbol, pos)
            total_pnl += pnl

        if self._initial_balance > 0:
            total_pnl_pct = (total_pnl / self._initial_balance) * 100.0
        else:
            total_pnl_pct = 0.0

        return total_pnl, total_pnl_pct

    async def _close_all_positions(self, positions: dict) -> Tuple[int, float]:
        """Закрывает все отслеживаемые позиции одновременно."""
        closed_count = 0
        total_pnl = 0.0
        close_results = []

        for symbol, pos in positions.items():
            try:
                qty = float(getattr(pos, "qty", 0) or 0)
                side = str(getattr(pos, "side", "Buy"))
                if qty <= 0:
                    continue
                pnl = await self._get_position_pnl(symbol, pos)

                if self.dry_run:
                    closed_count += 1
                    total_pnl += pnl
                    close_results.append((symbol, pnl, True))
                else:
                    result = await self.client.close_position(symbol, side, qty)
                    if result.get("success") or result.get("orderId"):
                        closed_count += 1
                        try:
                            if hasattr(self.client, "get_closed_pnl"):
                                closed = await self.client.get_closed_pnl(symbol, limit=1)
                                if closed:
                                    pnl = float(closed[0].get("closedPnl", pnl))
                        except Exception:
                            pass
                        total_pnl += pnl
                        close_results.append((symbol, pnl, True))
                    else:
                        close_results.append((symbol, 0.0, False))
            except Exception as e:
                logger.warning("PROFIT_LOCK close exception %s: %s", symbol, e)
                close_results.append((symbol, 0.0, False))

        await self._send_closure_notification(close_results, total_pnl)
        return closed_count, total_pnl

    async def _send_closure_notification(self, results: list, total_pnl: float) -> None:
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

        lines.extend(
            [
                "",
                "<b>Статистика защиты:</b>",
                f"  Активаций: {self._total_activations}",
                f"  Закрытий: {self._total_closures}",
                f"  Защищено: <code>${self._total_protected_pnl:+.2f}</code>",
                "",
                f"Cooldown: {self.cooldown_sec / 3600:.1f}ч",
            ]
        )
        text = "\n".join(lines)
        try:
            if hasattr(self.tg, "send_message"):
                await self.tg.send_message(text)
            elif hasattr(self.tg, "send"):
                await self.tg.send(text)
        except Exception as e:
            logger.warning("PROFIT_LOCK TG error: %s", e)

    async def check(self, positions: Union[Dict[str, Any], List[Dict[str, Any]]]) -> Optional[List[str]]:
        """Главная функция проверки. Возвращает список закрытых символов или None."""
        async with self._lock:
            filtered = self._filter_positions(positions)
            if not filtered:
                if self.state.status not in (LockStatus.INACTIVE, LockStatus.COOLDOWN):
                    self.state = LockState()
                return None

            if self.state.status == LockStatus.COOLDOWN:
                if self.state.last_close_time:
                    elapsed = (datetime.now(timezone.utc) - self.state.last_close_time).total_seconds()
                    if elapsed < self.cooldown_sec:
                        return None
                    self.state.status = LockStatus.INACTIVE
                    self.state.last_close_time = None

            current_pnl, current_pnl_pct = await self._calculate_portfolio_pnl(filtered)
            self.state.current_profit_usdt = current_pnl
            self.state.current_profit_pct = current_pnl_pct

            if self.state.status == LockStatus.INACTIVE:
                if current_pnl_pct >= self.min_profit_pct:
                    self.state.status = LockStatus.ARMED
                    self.state.max_profit_usdt = current_pnl
                    self.state.max_profit_pct = current_pnl_pct
                    self._total_activations += 1
                return None

            if self.state.status == LockStatus.ARMED:
                if current_pnl > self.state.max_profit_usdt:
                    self.state.max_profit_usdt = current_pnl
                    self.state.max_profit_pct = current_pnl_pct

                if self.state.max_profit_usdt > 0:
                    decline_pct = (
                        (self.state.max_profit_usdt - current_pnl) / self.state.max_profit_usdt
                    ) * 100.0
                    if decline_pct >= self.decline_threshold_pct:
                        self.state.status = LockStatus.DECLINING
                        self.state.decline_start_time = time.time()
                return None

            if self.state.status == LockStatus.DECLINING:
                if current_pnl > self.state.max_profit_usdt:
                    self.state.max_profit_usdt = current_pnl
                    self.state.max_profit_pct = current_pnl_pct
                    self.state.status = LockStatus.ARMED
                    self.state.decline_start_time = None
                    self.state.decline_duration_sec = 0.0
                    return None

                if self.state.max_profit_usdt > 0:
                    decline_pct = (
                        (self.state.max_profit_usdt - current_pnl) / self.state.max_profit_usdt
                    ) * 100.0
                    if decline_pct < self.decline_threshold_pct:
                        self.state.status = LockStatus.ARMED
                        self.state.decline_start_time = None
                        self.state.decline_duration_sec = 0.0
                        return None

                    if self.state.decline_start_time:
                        self.state.decline_duration_sec = time.time() - self.state.decline_start_time
                        if self.state.decline_duration_sec >= self.decline_duration_sec:
                            closed_symbols = list(filtered.keys())
                            closed_count, total_pnl = await self._close_all_positions(filtered)
                            self._total_closures += 1
                            self._total_protected_pnl += total_pnl
                            self.state.status = LockStatus.COOLDOWN
                            self.state.last_close_time = datetime.now(timezone.utc)
                            self.state.positions_closed = closed_count
                            self.state.total_closed_pnl = total_pnl
                            return closed_symbols

            return None

    def get_status(self) -> Dict[str, Any]:
        cooldown_remaining = 0.0
        if self.state.status == LockStatus.COOLDOWN and self.state.last_close_time:
            elapsed = (datetime.now(timezone.utc) - self.state.last_close_time).total_seconds()
            cooldown_remaining = max(0.0, self.cooldown_sec - elapsed)

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
            "inactive": "ВЫКЛ",
            "armed": "АКТИВНА",
            "declining": "СНИЖЕНИЕ",
            "triggered": "СРАБОТАЛА",
            "cooldown": "ПАУЗА",
        }
        lines = [
            "<b>PORTFOLIO PROFIT LOCK</b>",
            "",
            f"<b>Статус:</b> <code>{status_emoji.get(s['status'], s['status'])}</code>",
            f"<b>Текущая прибыль:</b> <code>${s['current_profit_usdt']:+.2f}</code> ({s['current_profit_pct']:+.1f}%)",
            f"<b>Пик прибыли:</b> <code>${s['max_profit_usdt']:+.2f}</code> ({s['max_profit_pct']:+.1f}%)",
        ]
        if s["status"] == "declining":
            lines.append(
                f"<b>Таймер:</b> {s['decline_duration_sec']:.0f}с / {self.decline_duration_sec:.0f}с"
            )
        if s["cooldown_remaining_sec"] > 0:
            lines.append(f"<b>Cooldown:</b> {s['cooldown_remaining_sec'] / 60:.0f}мин")
        lines.extend(
            [
                "",
                "<b>Статистика:</b>",
                f"  Активаций: {s['total_activations']}",
                f"  Закрытий: {s['total_closures']}",
                f"  Защищено: <code>${s['total_protected_pnl']:+.2f}</code>",
            ]
        )
        return "\n".join(lines)

    def reset(self) -> None:
        self.state = LockState()
