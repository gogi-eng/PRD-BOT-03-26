#!/usr/bin/env python3
"""
LiveControls — управление параметрами бота в реальном времени через Telegram.
"""
from __future__ import annotations
import threading
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING
from datetime import datetime, timedelta

if TYPE_CHECKING:
    from engine.risk_manager import RiskGuard


@dataclass
class LiveControls:
    """Параметры бота, изменяемые на лету через Telegram."""

    enabled: bool = True
    dry_run: bool = False
    signal_only: bool = True
    emergency: bool = False

    leverage: int = 10
    margin_total_pct: float = 10.0
    risk_per_trade_pct: float = 0.5
    tp_pct: float = 3.0
    sl_pct: float = 1.5
    max_positions: int = 3
    trailing_stop_pct: float = 2.0
    ai_enabled: bool = True
    rl_enabled: bool = True

    strategy_mode: str = "ai_fund"

    _guard: Optional["RiskGuard"] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    _session_trades: int = field(default=0, repr=False)
    _session_pnl: float = field(default=0.0, repr=False)
    _balance: float = field(default=0.0, repr=False)
    _current_positions: dict = field(default_factory=dict, repr=False)
    _unrealized_pnl: float = field(default=0.0, repr=False)
    _trade_history: list = field(default_factory=list, repr=False)
    _heatmap_snapshots: dict = field(default_factory=dict, repr=False)
    _candidate_snapshots: list = field(default_factory=list, repr=False)

    def set_guard(self, guard):
        self._guard = guard

    def guard_snapshot(self) -> dict:
        if self._guard:
            return self._guard.snapshot()
        return {
            "day": "N/A", "pnl_today": 0.0, "trades_today": 0,
            "blocked": False, "block_reason": "", "emergency_stop": self.emergency,
            "consecutive_losses": 0,
        }

    def add_trade(self, pnl: float, symbol: str = "", side: str = "", reason: str = ""):
        with self._lock:
            self._session_trades += 1
            self._session_pnl += pnl
            self._trade_history.append({
                "time": datetime.now(), "symbol": symbol,
                "side": side, "pnl": pnl, "reason": reason,
            })
            if len(self._trade_history) > 100:
                self._trade_history.pop(0)

    def set_balance(self, balance: float):
        with self._lock:
            self._balance = balance

    def get_balance(self) -> float:
        with self._lock:
            return self._balance

    def set_positions(self, positions: dict):
        with self._lock:
            self._current_positions = positions.copy()

    def set_unrealized_pnl(self, pnl: float):
        with self._lock:
            self._unrealized_pnl = pnl

    def get_strategy_mode_display(self) -> str:
        modes = {
            "ai_fund": "Transformer + Heatmap + Orderflow",
        }
        return modes.get(self.strategy_mode, self.strategy_mode)

    def set_heatmap(self, symbol: str, liq_analysis):
        with self._lock:
            self._heatmap_snapshots[symbol] = liq_analysis
            if len(self._heatmap_snapshots) > 20:
                first = next(iter(self._heatmap_snapshots.keys()))
                self._heatmap_snapshots.pop(first, None)

    def set_candidates(self, candidates: list):
        with self._lock:
            self._candidate_snapshots = candidates[:10]

    def heatmap_report(self) -> str:
        with self._lock:
            snapshots = list(self._heatmap_snapshots.items())
        if not snapshots:
            return "<b>HEATMAP</b>\n\nНет данных по ликвидациям"
        lines = ["<b>HEATMAP</b>"]
        for symbol, liq in snapshots[-6:]:
            target = liq.target_level or 0.0
            lines.append(
                f"\n<code>{symbol}</code> магнит=<b>{liq.magnet_direction}</b> "
                f"target=<code>{target:.4f}</code> dist=<code>{liq.distance_to_target_pct:.3f}%</code>"
            )
        return "\n".join(lines)

    def positions_report(self) -> str:
        with self._lock:
            positions = self._current_positions.copy()
        if not positions:
            return "<b>ПОЗИЦИИ</b>\n\nОткрытых позиций нет"
        lines = ["<b>ПОЗИЦИИ</b>"]
        for symbol, pos in positions.items():
            side = pos.get("side", "?")
            target = pos.get("heatmap_target", 0)
            partial_tp = "DONE" if pos.get("partial_tp_done") else f"${pos.get('partial_tp_price', 0):.4f}"
            lines.append(
                f"\n<code>{symbol}</code> {side} qty=<code>{pos.get('qty', 0):.6f}</code>"
                f"\nentry=<code>${pos.get('entry', 0):.4f}</code> pnl=<code>${pos.get('unrealized_pnl', 0):+.2f}</code>"
                f"\nSL=<code>${pos.get('stop_loss', 0):.4f}</code> TP=<code>${pos.get('take_profit', 0):.4f}</code> target=<code>${target:.4f}</code>"
                f"\norigin=<code>{pos.get('origin', 'bot')}</code> RL=<code>{pos.get('rl_action', 'hold')}</code>"
                f" partialTP=<code>{partial_tp}</code>"
            )
        return "\n".join(lines)

    def stats(self) -> str:
        snap = self.guard_snapshot()
        positions_lines = []
        with self._lock:
            if self._current_positions:
                positions_lines.append(f"\n<b>ОТКРЫТЫЕ ПОЗИЦИИ ({len(self._current_positions)})</b>")
                for symbol, pos in self._current_positions.items():
                    side = pos.get("side", "?")
                    direction = "ЛОНГ" if side.upper() in ["BUY", "LONG"] else "ШОРТ"
                    entry = pos.get("entry", 0)
                    unrealized = pos.get("unrealized_pnl", 0)
                    positions_lines.append(f"<code>{symbol}</code> {direction} вход=${entry:.4f} PnL=${unrealized:+.2f}")
            unrealized_total = self._unrealized_pnl

        lines = [
            "<b>СТАТИСТИКА СЕССИИ</b>",
            f"Баланс: <code>${self._balance:.2f}</code>",
            f"PnL сессии: <code>${self._session_pnl:+.2f}</code>",
            f"Нереализованный PnL: <code>${unrealized_total:+.2f}</code>",
            f"Сделок: <code>{self._session_trades}</code>",
            f"Стратегия: {self.get_strategy_mode_display()}",
            f"AI: {'ON' if self.ai_enabled else 'OFF'} | RL: {'ON' if self.rl_enabled else 'OFF'}",
        ]
        lines.extend(positions_lines)
        lines.extend([
            "\n<b>СЕГОДНЯ</b>",
            f"PnL: <code>${snap['pnl_today']:.2f}</code>",
            f"Сделок: <code>{snap['trades_today']}</code>",
            f"Блокировка: {'Да' if snap['blocked'] else 'Нет'}",
        ])
        return "\n".join(lines)

    def pnl_report(self) -> str:
        with self._lock:
            history = self._trade_history.copy()
            balance = self._balance
            session_pnl = self._session_pnl
            session_trades = self._session_trades
            unrealized = self._unrealized_pnl

        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_ago = now - timedelta(hours=1)
        pnl_today = pnl_hour = 0.0
        wins = losses = 0

        for trade in history:
            t = trade.get("time", now)
            p = trade.get("pnl", 0)
            if t >= today_start:
                pnl_today += p
                if p > 0:
                    wins += 1
                elif p < 0:
                    losses += 1
            if t >= hour_ago:
                pnl_hour += p

        total = wins + losses
        winrate = (wins / total * 100) if total > 0 else 0
        last5 = history[-5:]

        lines = [
            "<b>ПАНЕЛЬ PnL</b>",
            f"Баланс: <code>${balance:.2f}</code>",
            f"Нереализованный: <code>${unrealized:+.2f}</code>",
            f"Итого: <code>${balance + unrealized:.2f}</code>",
            f"\nPnL сессия: <code>${session_pnl:+.2f}</code>",
            f"PnL сегодня: <code>${pnl_today:+.2f}</code>",
            f"PnL за час: <code>${pnl_hour:+.2f}</code>",
            f"\nСделок: <code>{session_trades}</code>",
            f"Побед/Поражений: <code>{wins}/{losses}</code>",
            f"Winrate: <code>{winrate:.1f}%</code>",
        ]
        if self._candidate_snapshots:
            lines.append("\n<b>ТОП КАНДИДАТЫ</b>")
            for item in self._candidate_snapshots[:5]:
                lines.append(
                    f"{item.get('symbol', '?')}: <code>{item.get('signal_strength', 0):.2f}</code> "
                    f"w=<code>{item.get('capital_weight', 0):.2f}</code>"
                )
        if last5:
            lines.append("\n<b>ПОСЛЕДНИЕ СДЕЛКИ</b>")
            for trade in reversed(last5):
                sym = trade.get("symbol", "?")
                p = trade.get("pnl", 0)
                emoji = "+" if p >= 0 else "-"
                lines.append(f"{emoji} {sym}: <code>${p:+.2f}</code>")
        return "\n".join(lines)

    def reset_session(self):
        with self._lock:
            self._session_trades = 0
            self._session_pnl = 0.0
            self._trade_history = []
