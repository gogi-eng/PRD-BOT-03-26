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
    emergency: bool = False

    leverage: int = 10
    margin_total_pct: float = 10.0
    risk_per_trade_pct: float = 2.0
    tp_pct: float = 3.0
    sl_pct: float = 1.5
    max_positions: int = 3
    trailing_stop_pct: float = 2.0

    strategy_mode: str = "trend_pullback"

    _guard: Optional["RiskGuard"] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    _session_trades: int = field(default=0, repr=False)
    _session_pnl: float = field(default=0.0, repr=False)
    _balance: float = field(default=0.0, repr=False)
    _current_positions: dict = field(default_factory=dict, repr=False)
    _unrealized_pnl: float = field(default=0.0, repr=False)
    _trade_history: list = field(default_factory=list, repr=False)

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
            "trend_pullback": "Trend + Pullback + Liquidity",
        }
        return modes.get(self.strategy_mode, self.strategy_mode)

    def stats(self) -> str:
        snap = self.guard_snapshot()
        positions_lines = []
        with self._lock:
            if self._current_positions:
                positions_lines.append(f"\nOTKRYTYE POZICII ({len(self._current_positions)})")
                for symbol, pos in self._current_positions.items():
                    side = pos.get("side", "?")
                    entry = pos.get("entry", 0)
                    unrealized = pos.get("unrealized_pnl", 0)
                    positions_lines.append(f"{symbol} {side} entry=${entry:.4f} PnL=${unrealized:+.2f}")
            unrealized_total = self._unrealized_pnl

        lines = [
            "STATISTIKA SESSII",
            f"Balans: ${self._balance:.2f}",
            f"PnL sessii: ${self._session_pnl:.2f}",
            f"Unrealized PnL: ${unrealized_total:+.2f}",
            f"Sdelok: {self._session_trades}",
            f"Strategiya: {self.get_strategy_mode_display()}",
        ]
        lines.extend(positions_lines)
        lines.extend([
            "\nSEGODNYA",
            f"PnL: ${snap['pnl_today']:.2f}",
            f"Sdelok: {snap['trades_today']}",
            f"Blokirovka: {'Da' if snap['blocked'] else 'Net'}",
        ])
        return "\n".join(lines)

    def pnl_report(self) -> str:
        _ = self.guard_snapshot()
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
            "<b>PANEL PnL</b>",
            f"Balans: <code>${balance:.2f}</code>",
            f"Unrealized: <code>${unrealized:+.2f}</code>",
            f"Itogo: <code>${balance + unrealized:.2f}</code>",
            f"\nPnL sessiya: <code>${session_pnl:+.2f}</code>",
            f"PnL segodnya: <code>${pnl_today:+.2f}</code>",
            f"PnL poslednij chas: <code>${pnl_hour:+.2f}</code>",
            f"\nSdelok: <code>{session_trades}</code>",
            f"Wins/Losses: <code>{wins}/{losses}</code>",
            f"Winrate: <code>{winrate:.1f}%</code>",
        ]
        if last5:
            lines.append("\nPOSLEDNIE SDELKI")
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
