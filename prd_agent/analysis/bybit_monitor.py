"""
AI-монитор Bybit: графики, позиции, сопровождение сделок (read-only).

Не размещает ордера — только читает биржу и даёт текстовый анализ через LLM.
"""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, TYPE_CHECKING

from prd_agent.ai.llm_gateway import chat_async, load_llm_settings
from prd_agent.exchange.bybit_adapter import BybitAdapter
from prd_agent.exchange.bybit_read_adapter import build_read_exchange
from prd_agent.positions.liquidation_guard import distance_to_liq_pct

if TYPE_CHECKING:
    from prd_agent.engine.orchestrator import UnifiedOrchestrator

logger = logging.getLogger("prd_agent.bybit_monitor")


def _f(row: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _pct_change(old: float, new: float) -> float:
    if old <= 0:
        return 0.0
    return (new - old) / old * 100.0


def _sma(values: Sequence[float], period: int) -> float:
    if not values:
        return 0.0
    window = list(values[-period:])
    if not window:
        return 0.0
    return sum(window) / len(window)


def summarize_klines(klines: List[Dict[str, Any]], *, label: str = "") -> Dict[str, Any]:
    """Краткая тех. сводка по свечам (без внешних библиотек)."""
    if not klines:
        return {"label": label, "empty": True}
    closes = [_f(k, "close") for k in klines if _f(k, "close") > 0]
    highs = [_f(k, "high") for k in klines]
    lows = [_f(k, "low") for k in klines]
    volumes = [_f(k, "volume") for k in klines]
    if not closes:
        return {"label": label, "empty": True}
    last = closes[-1]
    first = closes[0]
    sma_fast = _sma(closes, 8)
    sma_slow = _sma(closes, 21)
    trend = "боковик"
    if sma_fast > sma_slow * 1.001:
        trend = "вверх"
    elif sma_fast < sma_slow * 0.999:
        trend = "вниз"
    vol_recent = sum(volumes[-4:]) / max(1, min(4, len(volumes)))
    vol_prev = sum(volumes[-8:-4]) / max(1, min(4, len(volumes[-8:-4]) or [1]))
    vol_trend = "стабильно"
    if vol_recent > vol_prev * 1.15:
        vol_trend = "растёт"
    elif vol_recent < vol_prev * 0.85:
        vol_trend = "падает"
    return {
        "label": label,
        "last": last,
        "change_pct": round(_pct_change(first, last), 2),
        "high": max(highs) if highs else last,
        "low": min(lows) if lows else last,
        "trend": trend,
        "sma_fast": round(sma_fast, 6),
        "sma_slow": round(sma_slow, 6),
        "volume_trend": vol_trend,
        "bars": len(closes),
    }


def format_position_line(row: Mapping[str, Any]) -> str:
    sym = str(row.get("symbol", "?"))
    side = str(row.get("side", "?"))
    size = _f(row, "size")
    entry = _f(row, "avgPrice") or _f(row, "entryPrice")
    mark = _f(row, "markPrice")
    upnl = _f(row, "unrealisedPnl")
    sl = _f(row, "stopLoss")
    tp = _f(row, "takeProfit")
    liq = _f(row, "liqPrice")
    lev = int(_f(row, "leverage", 0))
    dist_liq = distance_to_liq_pct(side, mark, liq)
    sl_dist = _pct_change(mark, sl) if sl > 0 and mark > 0 else 0.0
    tp_dist = _pct_change(mark, tp) if tp > 0 and mark > 0 else 0.0
    if str(side).upper() in ("SELL", "SHORT"):
        sl_dist = -sl_dist
        tp_dist = -tp_dist
    return (
        f"{sym} {side} x{lev} size={size:g} entry={entry:g} mark={mark:g} "
        f"uPnL={upnl:+.2f} SL={sl:g}({sl_dist:+.2f}%) TP={tp:g}({tp_dist:+.2f}%) "
        f"liq={liq:g} dist_liq={dist_liq:.2f}%"
    )


class BybitMonitorAgent:
    """Read-only мониторинг рынка и сопровождение открытых позиций."""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        bm = cfg.get("bybit_monitor", {}) if isinstance(cfg.get("bybit_monitor"), dict) else {}
        self.enabled = bool(bm.get("enabled", True))
        self.interval_sec = float(bm.get("interval_sec", 300))
        self.notify_telegram = bool(bm.get("notify_telegram", False))
        self.llm_summary = bool(bm.get("llm_summary", True))
        self.kline_interval = str(bm.get("kline_interval", "15"))
        self.kline_limit = int(bm.get("kline_limit", 96))
        self.include_funding = bool(bm.get("include_funding", True))
        self.include_oi = bool(bm.get("include_oi", True))
        self.include_liquidations = bool(bm.get("include_liquidations", True))
        self.alert_upnl_change_usdt = float(bm.get("alert_upnl_change_usdt", 15.0))
        self.max_symbols = int(bm.get("max_symbols", 8))
        self._llm = load_llm_settings(cfg)
        self._read_exchange = build_read_exchange(cfg)
        self._last_snapshot_upnl = 0.0
        self._last_notify_at = 0.0

    def uses_dedicated_read_key(self) -> bool:
        return self._read_exchange is not None

    def _exchange(self, orch: "UnifiedOrchestrator") -> BybitAdapter:
        return self._read_exchange or orch.exchange

    def _resolve_symbols(
        self,
        orch: "UnifiedOrchestrator",
        positions: List[Dict[str, Any]],
    ) -> List[str]:
        seen: List[str] = []
        for sym in list(orch.symbols) + [str(p.get("symbol", "")) for p in positions]:
            s = str(sym or "").upper().strip()
            if s and s not in seen:
                seen.append(s)
            if len(seen) >= self.max_symbols:
                break
        return seen or ["BTCUSDT", "ETHUSDT"]

    async def collect_snapshot(self, orch: "UnifiedOrchestrator") -> Dict[str, Any]:
        ex = self._exchange(orch)
        balance = await ex.get_balance()
        available = await ex.get_available_balance()
        positions = list(await ex.get_positions())
        symbols = self._resolve_symbols(orch, positions)
        charts: Dict[str, Dict[str, Any]] = {}
        funding: Dict[str, Any] = {}
        oi: Dict[str, Any] = {}
        liquidations: Dict[str, int] = {}
        for sym in symbols:
            try:
                klines = await ex.get_klines(sym, interval=self.kline_interval, limit=self.kline_limit)
                charts[sym] = summarize_klines(klines, label=f"{sym} {self.kline_interval}m")
            except Exception as exc:
                logger.warning("bybit_monitor klines %s: %s", sym, exc)
                charts[sym] = {"label": sym, "empty": True, "error": str(exc)}
            if self.include_funding:
                try:
                    funding[sym] = await ex.get_funding_rate(sym)
                except Exception as exc:
                    logger.warning("bybit_monitor funding %s: %s", sym, exc)
            if self.include_oi:
                try:
                    hist = await ex.get_open_interest_history(sym, interval="1h", limit=6)
                    if hist:
                        oi[sym] = hist[-1]
                except Exception as exc:
                    logger.warning("bybit_monitor oi %s: %s", sym, exc)
            if self.include_liquidations:
                try:
                    liqs = await ex.get_recent_liquidations(sym, limit=10)
                    liquidations[sym] = len(liqs)
                except Exception as exc:
                    logger.warning("bybit_monitor liq %s: %s", sym, exc)
        upnl = sum(_f(p, "unrealisedPnl") for p in positions)
        return {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "read_key_mode": self.uses_dedicated_read_key(),
            "balance_usdt": round(balance, 2),
            "available_usdt": round(available, 2),
            "open_positions": len(positions),
            "upnl_usdt": round(upnl, 2),
            "positions": positions,
            "symbols": symbols,
            "charts": charts,
            "funding": funding,
            "open_interest": oi,
            "liquidation_events": liquidations,
            "risk": orch.risk.snapshot(),
        }

    def _format_data_block(self, snap: Dict[str, Any]) -> str:
        lines: List[str] = []
        mode = "read-only ключ" if snap.get("read_key_mode") else "основной ключ (только чтение)"
        lines.append(f"Режим API: {mode}")
        lines.append(
            f"Баланс: {snap.get('balance_usdt')} USDT | доступно: {snap.get('available_usdt')} USDT"
        )
        lines.append(
            f"Позиций: {snap.get('open_positions')} | суммарный uPnL: {snap.get('upnl_usdt')} USDT"
        )
        risk = snap.get("risk") or {}
        lines.append(
            f"Риск бота: status={risk.get('status')} blocked={risk.get('blocked')} "
            f"PnL сегодня UTC={risk.get('pnl_today_usdt')} USDT"
        )
        positions = snap.get("positions") or []
        if positions:
            lines.append("\nОткрытые позиции:")
            for row in positions[:10]:
                lines.append(f"- {format_position_line(row)}")
        else:
            lines.append("\nОткрытых позиций нет.")
        charts = snap.get("charts") or {}
        if charts:
            lines.append("\nГрафики:")
            for sym, ch in charts.items():
                if ch.get("empty"):
                    lines.append(f"- {sym}: нет данных")
                    continue
                lines.append(
                    f"- {sym}: цена={ch.get('last')} change={ch.get('change_pct')}% "
                    f"trend={ch.get('trend')} vol={ch.get('volume_trend')} "
                    f"hi={ch.get('high')} lo={ch.get('low')}"
                )
        funding = snap.get("funding") or {}
        if funding:
            lines.append("\nFunding:")
            for sym, row in funding.items():
                if not row:
                    continue
                rate = row.get("fundingRate") or row.get("funding_rate") or row.get("rate")
                lines.append(f"- {sym}: {rate}")
        liqs = snap.get("liquidation_events") or {}
        if liqs:
            lines.append("\nЛиквидации (события в буфере):")
            for sym, cnt in liqs.items():
                lines.append(f"- {sym}: {cnt}")
        return "\n".join(lines)

    def _build_prompt(self, snap: Dict[str, Any]) -> str:
        data = self._format_data_block(snap)
        has_positions = bool(snap.get("positions"))
        focus = (
            "Сфокусируйся на сопровождении ОТКРЫТЫХ позиций: риск, SL/TP, дистанция до ликвидации."
            if has_positions
            else "Позиций нет — дай обзор рынка по символам из скана."
        )
        return f"""Ты AI-наблюдатель perpetual futures Bybit (read-only, ордера не ставишь).
{focus}

Данные с биржи (UTC {snap.get('ts_utc')}):
{data}

Формат ответа (русский, 6–10 пунктов):
1) <b>Общая картина</b> — тренд BTC/ETH и риск-режим
2) <b>Позиции</b> — по каждой: держать / осторожность / рассмотреть фиксацию (без «вложите всё»)
3) <b>Уровни</b> — что важно по SL/TP/ликвидации
4) <b>Рынок</b> — funding/объём/импульс если видно
5) <b>Действия</b> — 2–3 конкретных шага для трейдера (кнопки панели, не авто-ордер)
"""

    async def build_report(self, orch: "UnifiedOrchestrator") -> str:
        if not self.enabled:
            return "Модуль bybit_monitor отключён в config.yaml"
        try:
            snap = await self.collect_snapshot(orch)
        except Exception as exc:
            logger.exception("bybit_monitor collect: %s", exc)
            return f"<b>📡 Bybit AI</b>\n\nОшибка чтения биржи: {html.escape(str(exc))}"
        data_block = self._format_data_block(snap)
        if not self.llm_summary:
            safe = html.escape(data_block[:3500])
            key_note = "read-key" if snap.get("read_key_mode") else "main key (read)"
            return f"<b>📡 Bybit монитор ({key_note})</b>\n\n<pre>{safe}</pre>"
        if not self._llm.uses_fcc and not self._llm.openrouter_api_key:
            safe = html.escape(data_block[:3500])
            return (
                "<b>📡 Bybit монитор</b>\n\n"
                "AI не настроен (нужен OPENROUTER_API_KEY или FCC).\n\n"
                f"<pre>{safe}</pre>"
            )
        try:
            text, err = await chat_async(
                self._llm,
                system=(
                    "Ты помощник трейдера фьючерсов Bybit. Read-only: не открывай сделки. "
                    "Кратко, по делу, на русском. HTML <b> допустим. "
                    "Не обещай прибыль. Указывай риски."
                ),
                user=self._build_prompt(snap),
                max_tokens=900,
                temperature=0.15,
                title="PRD-BOT Bybit Monitor",
            )
            if err:
                return f"<b>📡 Bybit AI</b>\n\nОшибка LLM: {html.escape(err)}"
            if not text:
                return "<b>📡 Bybit AI</b>\n\nПустой ответ модели."
            safe = html.escape(text[:3500])
            backend = "Free Claude Code" if self._llm.uses_fcc else "OpenRouter"
            key_note = "🔑 read-only" if snap.get("read_key_mode") else "👁 основной ключ"
            header = (
                f"<b>📡 Bybit AI ({backend}, {key_note})</b>\n"
                f"<i>uPnL {snap.get('upnl_usdt'):+.2f} USDT | "
                f"позиций {snap.get('open_positions')}</i>\n\n"
            )
            return header + safe
        except Exception as exc:
            logger.exception("bybit_monitor llm: %s", exc)
            return f"<b>📡 Bybit AI</b>\n\nОшибка анализа: {html.escape(str(exc))}"

    async def maybe_scheduled_alert(self, orch: "UnifiedOrchestrator") -> Optional[str]:
        """Фоновое уведомление при заметном изменении uPnL (если notify_telegram)."""
        if not self.enabled or not self.notify_telegram:
            return None
        now = time.time()
        if now - self._last_notify_at < max(120.0, self.interval_sec * 0.5):
            return None
        try:
            snap = await self.collect_snapshot(orch)
        except Exception as exc:
            logger.warning("bybit_monitor alert collect: %s", exc)
            return None
        upnl = float(snap.get("upnl_usdt") or 0)
        delta = abs(upnl - self._last_snapshot_upnl)
        self._last_snapshot_upnl = upnl
        if snap.get("open_positions", 0) <= 0:
            return None
        if delta < self.alert_upnl_change_usdt:
            return None
        self._last_notify_at = now
        if not self.llm_summary:
            return (
                f"<b>📡 Bybit: изменение uPnL</b>\n"
                f"Суммарный uPnL: <b>{upnl:+.2f}</b> USDT (Δ {delta:.2f})\n"
                f"<i>Нажмите «Bybit AI» в панели для полного разбора.</i>"
            )
        short = await self.build_report(orch)
        return short
