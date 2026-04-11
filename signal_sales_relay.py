#!/usr/bin/env python3
"""Bridge from main TradingBot to paid-signals storage."""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote_plus

try:
    from core.config import BotConfig
except ModuleNotFoundError:  # pragma: no cover - import path for test runner
    from bot.core.config import BotConfig

try:
    from signal_sales_store import SignalPayload, SignalSalesStore
except ModuleNotFoundError:  # pragma: no cover - import path for test runner
    from bot.signal_sales_store import SignalPayload, SignalSalesStore

logger = logging.getLogger("signal_sales_relay")


class SignalSalesRelay:
    """Persists generated signals for distribution by signal_sales_bot."""

    def __init__(self, cfg: BotConfig):
        self.enabled = bool(cfg.get("signal_sales", "enabled", default=False))
        db_path = str(cfg.get("signal_sales", "db_path", default="signal_sales.db"))
        base_dir = Path(__file__).resolve().parent
        resolved_db_path = str((base_dir / db_path).resolve()) if not Path(db_path).is_absolute() else db_path
        self.store = SignalSalesStore(resolved_db_path)
        self.default_exchange = str(cfg.get("signal_sales", "exchange_name", default="BYBIT"))
        self.default_leverage = str(cfg.get("signal_sales", "default_leverage", default="10x"))
        self.default_order_type = str(cfg.get("signal_sales", "default_order_type", default="Limit"))
        self.tv_base_url = str(cfg.get("signal_sales", "tradingview_base_url", default="https://www.tradingview.com/chart"))
        self.tv_layout_id = str(cfg.get("signal_sales", "tradingview_layout_id", default=""))
        self.tv_exchange_prefix = str(cfg.get("signal_sales", "tradingview_exchange_prefix", default="BYBIT"))
        self.tv_symbol_suffix = str(cfg.get("signal_sales", "tradingview_symbol_suffix", default=".P"))

    def _build_tradingview_deep_link(
        self,
        symbol: str,
        timeframe: str,
        side: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
    ) -> str:
        clean_symbol = str(symbol).upper().strip()
        if not clean_symbol:
            return ""
        symbol_code = f"{self.tv_exchange_prefix}:{clean_symbol}{self.tv_symbol_suffix}"
        tf = str(timeframe or "5").strip()
        side_norm = str(side or "").upper()
        levels_blob = (
            f"SIDE={side_norm};"
            f"ENTRY={entry:.6f};"
            f"SL={stop_loss:.6f};"
            f"TP={take_profit:.6f};"
            f"TF={tf};"
            "NOTE=Auto template levels"
        )
        root = self.tv_base_url.rstrip("/")
        if self.tv_layout_id:
            root = f"{root}/{quote_plus(self.tv_layout_id)}"
        return (
            f"{root}/?symbol={quote_plus(symbol_code)}"
            f"&interval={quote_plus(tf)}"
            f"&layout={quote_plus(self.tv_layout_id)}"
            f"&template=levels"
            f"&levels={quote_plus(levels_blob)}"
        )

    async def publish_signal(
        self,
        symbol: str,
        side: str,
        entry: float | None = None,
        entry_price: float | None = None,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        exchange: str | None = None,
        timeframe: str = "",
        leverage: str | None = None,
        order_type: str | None = None,
        chart_url: str | None = None,
        rr_ratio: float | None = None,
    ) -> None:
        if not self.enabled:
            return
        entry_value = entry if entry is not None else entry_price
        if entry_value is None:
            entry_value = 0.0

        normalized_symbol = str(symbol).upper()
        rr_value = float(rr_ratio) if rr_ratio is not None else 0.0
        if rr_value <= 0:
            risk = abs(float(entry_value) - float(stop_loss))
            reward = abs(float(take_profit) - float(entry_value))
            rr_value = (reward / risk) if risk > 0 else 0.0

        chart = (chart_url or "").strip()
        if not chart:
            chart = self._build_tradingview_deep_link(
                symbol=normalized_symbol,
                timeframe=timeframe,
                side=str(side).upper(),
                entry=float(entry_value),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
            )

        payload = SignalPayload(
            symbol=normalized_symbol,
            side=str(side).upper(),
            entry_price=float(entry_value),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            exchange=str(exchange or self.default_exchange).upper(),
            leverage=str(leverage or self.default_leverage),
            order_type=str(order_type or self.default_order_type),
            chart_url=chart,
            rr_ratio=float(rr_value),
            source="main_bot" if not timeframe else f"main_bot_tf_{timeframe}",
        )
        signal_id = self.store.insert_signal(payload)
        logger.info(
            "[SignalSalesRelay] queued signal_id=%s %s %s entry=%s sl=%s tp=%s",
            signal_id,
            payload.symbol,
            payload.side,
            payload.entry_price,
            payload.stop_loss,
            payload.take_profit,
        )

