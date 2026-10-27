"""Крупные заявки в стакане и волны ликвидаций: только сообщение, без ордера."""
from __future__ import annotations

import asyncio
import html
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("prd_agent.liquidity_alerts")

FEE_ROUNDTRIP_PCT = 0.11


@dataclass(frozen=True)
class AlertSettings:
    enabled: bool = False
    interval_sec: float = 60.0
    max_symbols: int = 8
    min_wall_usd: float = 25000.0
    wall_multiple: float = 5.0
    wall_persist_sec: float = 45.0
    min_liq_usd: float = 20000.0
    liq_window_sec: float = 180.0
    notify_cooldown_sec: float = 1800.0
    orderbook_depth: int = 50


@dataclass(frozen=True)
class LiquidityAlert:
    kind: str
    symbol: str
    side: str
    usd: float
    text: str
    log_line: str


@dataclass
class AlertState:
    wall_first_seen: Dict[Tuple[str, str, int], float] = field(default_factory=dict)
    cooldown_until: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class LiqPrint:
    ts_ms: int
    symbol: str
    position_side: str
    usd: float


@dataclass(frozen=True)
class FadeTrade:
    symbol: str
    position_side: str
    entry_side: str
    entry_ts_ms: int
    exit_ts_ms: int
    gross_pct: float
    net_pct: float


def read_settings(cfg: Mapping[str, Any]) -> AlertSettings:
    raw = cfg.get("liquidity_alerts")
    block = raw if isinstance(raw, dict) else {}

    def _num(name: str, default: float, lo: float, hi: float) -> float:
        try:
            value = float(block.get(name, default))
        except (TypeError, ValueError):
            value = default
        return min(hi, max(lo, value))

    return AlertSettings(
        enabled=bool(block.get("enabled", False)),
        interval_sec=_num("interval_sec", 60.0, 20.0, 600.0),
        max_symbols=int(_num("max_symbols", 8, 1, 24)),
        min_wall_usd=_num("min_wall_usd", 25000.0, 1000.0, 50_000_000.0),
        wall_multiple=_num("wall_multiple", 5.0, 2.0, 50.0),
        wall_persist_sec=_num("wall_persist_sec", 45.0, 0.0, 600.0),
        min_liq_usd=_num("min_liq_usd", 20000.0, 1000.0, 50_000_000.0),
        liq_window_sec=_num("liq_window_sec", 180.0, 30.0, 900.0),
        notify_cooldown_sec=_num("notify_cooldown_sec", 1800.0, 60.0, 86400.0),
        orderbook_depth=int(_num("orderbook_depth", 50, 10, 200)),
    )


def liquidity_alerts_enabled(cfg: Mapping[str, Any]) -> bool:
    return read_settings(cfg).enabled


def _levels(raw: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            price = float(row[0])
            size = float(row[1])
        except (TypeError, ValueError):
            continue
        if price > 0 and size > 0:
            out.append((price, size))
    return out


def _price_bucket(price: float) -> int:
    if price <= 0:
        return 0
    return int(round(math.log(price) / math.log(1.001)))


def _event_fields(event: Mapping[str, Any]) -> Tuple[str, float, int]:
    side = str(event.get("side") or "").strip()
    try:
        price = float(event.get("price") or 0)
        size = float(event.get("size") or event.get("qty") or 0)
        ts = int(event.get("timestamp") or event.get("T") or 0)
    except (TypeError, ValueError):
        return side, 0.0, 0
    if ts and ts < 10**12:
        ts *= 1000
    usd = price * size if price > 0 and size > 0 else 0.0
    return side, usd, ts


def _position_side(side: str) -> str:
    text = side.strip().lower()
    if text in ("buy", "long"):
        return "Buy"
    if text in ("sell", "short"):
        return "Sell"
    return ""


def fade_entry_side(position_side: str) -> str:
    """Вход против уже случившейся ликвидации.

    В ленте Bybit Buy значит: ликвидировали лонги, цена падала.
    Через 10–15 минут проверка покупает. Sell в ленте — ликвидировали шорты,
    проверка продаёт.
    """
    return _position_side(position_side)


def _cooldown_ready(state: AlertState, key: str, now: float, cooldown_sec: float) -> bool:
    return now >= state.cooldown_until.get(key, 0.0)


def _mark_sent(state: AlertState, key: str, now: float, cooldown_sec: float) -> None:
    state.cooldown_until[key] = now + cooldown_sec


def collect_alerts(
    books: Mapping[str, Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    state: AlertState,
    now: float,
    settings: AlertSettings,
) -> List[LiquidityAlert]:
    alerts: List[LiquidityAlert] = []
    seen_walls: set[Tuple[str, str, int]] = set()
    for symbol, book in books.items():
        sym = str(symbol or "").upper()
        if not sym or not isinstance(book, Mapping):
            continue
        for side_name, raw in (("bid", book.get("bids")), ("ask", book.get("asks"))):
            levels = _levels(raw)
            if not levels:
                continue
            avg = sum(size for _, size in levels) / len(levels)
            if avg <= 0:
                continue
            threshold = avg * settings.wall_multiple
            best_usd = 0.0
            best_price = 0.0
            for price, size in levels:
                if size < threshold:
                    continue
                usd = price * size
                if usd >= settings.min_wall_usd and usd > best_usd:
                    best_usd = usd
                    best_price = price
            if best_usd <= 0:
                continue
            bucket = _price_bucket(best_price)
            key = (sym, side_name, bucket)
            seen_walls.add(key)
            first = state.wall_first_seen.get(key)
            if first is None:
                state.wall_first_seen[key] = now
                continue
            if now - first < settings.wall_persist_sec:
                continue
            cool = f"wall:{sym}:{side_name}"
            if not _cooldown_ready(state, cool, now, settings.notify_cooldown_sec):
                continue
            _mark_sent(state, cool, now, settings.notify_cooldown_sec)
            title = "покупку" if side_name == "bid" else "продажу"
            text = (
                f"Крупная заявка на {title} {html.escape(sym)}: "
                f"около {best_price:.6g}, примерно {best_usd:,.0f} USDT, "
                f"стоит в стакане дольше {settings.wall_persist_sec:.0f} сек. "
                "Новую сделку из-за этого не открываю."
            )
            alerts.append(
                LiquidityAlert(
                    kind="wall",
                    symbol=sym,
                    side=side_name,
                    usd=best_usd,
                    text=text,
                    log_line=f"wall {sym} {side_name} usd={best_usd:.0f} px={best_price:.6g}",
                )
            )

    for key in list(state.wall_first_seen):
        if key not in seen_walls:
            state.wall_first_seen.pop(key, None)

    now_ms = int(now * 1000)
    window_ms = int(settings.liq_window_sec * 1000)
    grouped: Dict[str, Dict[str, float]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        symbol = str(event.get("symbol") or "").upper()
        side, usd, ts = _event_fields(event)
        position_side = _position_side(side)
        if not symbol or not position_side or usd <= 0 or ts <= 0:
            continue
        if now_ms - ts > window_ms or ts > now_ms + 5_000:
            continue
        bucket = grouped.setdefault(symbol, {"Buy": 0.0, "Sell": 0.0})
        bucket[position_side] += usd

    for symbol, sides in grouped.items():
        position_side = "Buy" if sides["Buy"] >= sides["Sell"] else "Sell"
        usd = sides[position_side]
        if usd < settings.min_liq_usd:
            continue
        cool = f"liq:{symbol}:{position_side}"
        if not _cooldown_ready(state, cool, now, settings.notify_cooldown_sec):
            continue
        _mark_sent(state, cool, now, settings.notify_cooldown_sec)
        who = "покупки (лонги)" if position_side == "Buy" else "продажи (шорты)"
        text = (
            f"Волна ликвидаций {html.escape(symbol)}: закрыли {who} "
            f"примерно на {usd:,.0f} USDT за {settings.liq_window_sec:.0f} сек. "
            "Новую сделку из-за этого не открываю."
        )
        alerts.append(
            LiquidityAlert(
                kind="liq",
                symbol=symbol,
                side=position_side,
                usd=usd,
                text=text,
                log_line=f"liq {symbol} {position_side} usd={usd:.0f}",
            )
        )
    return alerts


def price_at(candles: Sequence[Tuple[int, float]], ts_ms: int, bar_ms: int = 60_000) -> Optional[float]:
    if not candles:
        return None
    chosen: Optional[float] = None
    for start, close in candles:
        if start <= ts_ms < start + bar_ms:
            return close
        if start <= ts_ms:
            chosen = close
        elif chosen is not None:
            break
    return chosen


def simulate_fade(
    events: Sequence[LiqPrint],
    candles: Mapping[str, Sequence[Tuple[int, float]]],
    *,
    delay_min: int,
    hold_min: int,
    min_usd: float,
    fee_roundtrip_pct: float = FEE_ROUNDTRIP_PCT,
    non_overlapping: bool = True,
) -> List[FadeTrade]:
    """Вход через delay_min против ликвидации, выход через hold_min. Цена — закрытие минутной свечи."""
    delay_ms = int(delay_min) * 60_000
    hold_ms = int(hold_min) * 60_000
    ordered = sorted(events, key=lambda item: (item.symbol, item.ts_ms))
    busy_until: Dict[str, int] = {}
    trades: List[FadeTrade] = []
    for event in ordered:
        if event.usd < min_usd:
            continue
        entry_side = fade_entry_side(event.position_side)
        series = candles.get(event.symbol)
        if not entry_side or not series:
            continue
        entry_ts = event.ts_ms + delay_ms
        if non_overlapping and entry_ts < busy_until.get(event.symbol, 0):
            continue
        exit_ts = entry_ts + hold_ms
        entry_px = price_at(series, entry_ts)
        exit_px = price_at(series, exit_ts)
        if entry_px is None or exit_px is None or entry_px <= 0 or exit_px <= 0:
            continue
        if entry_side == "Buy":
            gross = (exit_px - entry_px) / entry_px * 100.0
        else:
            gross = (entry_px - exit_px) / entry_px * 100.0
        trades.append(
            FadeTrade(
                symbol=event.symbol,
                position_side=event.position_side,
                entry_side=entry_side,
                entry_ts_ms=entry_ts,
                exit_ts_ms=exit_ts,
                gross_pct=gross,
                net_pct=gross - fee_roundtrip_pct,
            )
        )
        if non_overlapping:
            busy_until[event.symbol] = exit_ts
    return trades


def summarize_fade(trades: Sequence[FadeTrade]) -> Dict[str, float]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "avg_net_pct": 0.0, "sum_net_pct": 0.0}
    wins = sum(1 for trade in trades if trade.net_pct > 0)
    total = sum(trade.net_pct for trade in trades)
    return {
        "n": float(n),
        "wins": float(wins),
        "win_rate": wins / n,
        "avg_net_pct": total / n,
        "sum_net_pct": total,
    }


async def run_liquidity_alert_loop(
    exchange: Any,
    notifier: Any,
    get_symbols: Callable[[], Sequence[str]],
    cfg: Mapping[str, Any],
    is_running: Callable[[], bool],
    log: Optional[logging.Logger] = None,
) -> None:
    log = log or logger
    settings = read_settings(cfg)
    log.info(
        "Liquidity alerts: только Telegram, без входа. стена от %.0f USDT, ликвидации от %.0f USDT",
        settings.min_wall_usd,
        settings.min_liq_usd,
    )
    await asyncio.sleep(20)
    state = AlertState()
    while is_running():
        try:
            symbols = []
            seen = set()
            for raw in list(get_symbols() or [])[: settings.max_symbols]:
                sym = str(raw or "").upper()
                if sym and sym not in seen:
                    seen.add(sym)
                    symbols.append(sym)
            # Список allLiquidation собирает оркестратор. Здесь свой короткий список
            # закрывал бы сокет и обнулял ленту.
            books: Dict[str, Mapping[str, Any]] = {}
            events: List[Dict[str, Any]] = []
            for sym in symbols:
                book = await exchange.get_orderbook(
                    sym,
                    limit=settings.orderbook_depth,
                    lazy=False,
                )
                books[sym] = book if isinstance(book, Mapping) else {}
                if hasattr(exchange, "get_recent_liquidations"):
                    liqs = await exchange.get_recent_liquidations(sym, limit=250)
                    for item in liqs or []:
                        if isinstance(item, Mapping):
                            row = dict(item)
                            row["symbol"] = sym
                            events.append(row)
            alerts = collect_alerts(books, events, state, time.time(), settings)
            for alert in alerts:
                log.info("Liquidity alert %s", alert.log_line)
                await notifier.send(alert.text)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Liquidity alerts: цикл не прошёл", exc_info=True)
        await asyncio.sleep(settings.interval_sec)
