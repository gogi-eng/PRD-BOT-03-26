"""
Периодическая проверка: у открытых позиций на бирже должны быть SL и TP.

Если на Bybit stopLoss/takeProfit пустые — логируем маркер и доставляем уровни
через update_stop_loss / update_take_profit (тот же путь, что trading-stop).

SL/TP на бирже ≠ автозакрытие Companion / time-stop: для manual тоже защищаем
капитал, если include_manual=true (по умолчанию).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from prd_agent.risk.rr_enforce import enforce_min_rr_levels

logger = logging.getLogger("prd_agent.positions.sl_tp_guard")

LOG_MARKER = "SL/TP guard"
MISSING_MARKER = "Missing SL/TP on position"


@dataclass
class SlTpGuardConfig:
    enabled: bool = True
    interval_sec: float = 60.0
    include_manual: bool = True
    default_sl_pct: float = 0.5
    default_tp_pct: float = 1.0
    min_rr: float = 2.0

    @classmethod
    def from_cfg(cls, cfg: Dict[str, Any]) -> "SlTpGuardConfig":
        positions = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
        raw = positions.get("sl_tp_guard") if isinstance(positions.get("sl_tp_guard"), dict) else {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            interval_sec=max(5.0, float(raw.get("interval_sec", 60) or 60)),
            include_manual=bool(raw.get("include_manual", True)),
            default_sl_pct=max(0.05, float(raw.get("default_sl_pct", 0.5) or 0.5)),
            default_tp_pct=max(0.05, float(raw.get("default_tp_pct", 1.0) or 1.0)),
            min_rr=max(0.0, float(raw.get("min_rr", 2.0) or 0.0)),
        )


def _f(val: Any) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def exchange_sl_tp(row: Dict[str, Any]) -> Tuple[float, float]:
    """Уровни, уже стоящие на бирже (Bybit camelCase)."""
    return _f(row.get("stopLoss")), _f(row.get("takeProfit"))


def missing_sides(ex_sl: float, ex_tp: float) -> Tuple[bool, bool]:
    return ex_sl <= 0, ex_tp <= 0


def compute_fallback_levels(
    *,
    side: str,
    entry: float,
    bot_sl: float = 0.0,
    bot_tp: float = 0.0,
    default_sl_pct: float = 0.5,
    default_tp_pct: float = 1.0,
    min_rr: float = 2.0,
) -> Tuple[float, float]:
    """
    Целевые SL/TP: сначала уровни бота (registry), иначе % от входа + min_rr.
    Не меняет геометрию уже заданных bot_sl/bot_tp без нужды — только тянет TP под RR.
    """
    e = _f(entry)
    if e <= 0:
        return 0.0, 0.0
    side_u = str(side or "").strip().lower()
    is_buy = side_u in ("buy", "long")

    sl = _f(bot_sl)
    tp = _f(bot_tp)
    if sl <= 0:
        if is_buy:
            sl = e * (1.0 - default_sl_pct / 100.0)
        else:
            sl = e * (1.0 + default_sl_pct / 100.0)
    if tp <= 0:
        if is_buy:
            tp = e * (1.0 + default_tp_pct / 100.0)
        else:
            tp = e * (1.0 - default_tp_pct / 100.0)

    if min_rr > 0:
        new_sl, new_tp, ok = enforce_min_rr_levels(
            side="Buy" if is_buy else "Sell",
            entry=e,
            stop_loss=sl,
            take_profit=tp,
            min_rr=min_rr,
        )
        if ok:
            sl, tp = new_sl, new_tp
    return sl, tp


def should_guard_origin(origin: str, include_manual: bool) -> bool:
    o = str(origin or "manual").lower()
    if o == "bot":
        return True
    return bool(include_manual)


class SlTpExchangeGuard:
    """Троттлинг попыток восстановления по символу."""

    def __init__(self) -> None:
        self._last_attempt: Dict[str, float] = {}

    def apply_config(self, cfg: SlTpGuardConfig) -> None:
        self.cfg = cfg

    def _due(self, symbol: str, now: float) -> bool:
        last = self._last_attempt.get(symbol, 0.0)
        return (now - last) >= float(self.cfg.interval_sec)

    async def ensure(
        self,
        exchange: Any,
        positions: List[Dict[str, Any]],
        *,
        bot_levels: Dict[str, Dict[str, float]],
        bot_symbols: set,
        origin_of: Optional[Any] = None,
    ) -> List[str]:
        """
        Проверяет live positions; при отсутствии SL/TP на бирже — ставит.
        origin_of(sym) -> 'bot'|'manual' (опционально).
        """
        notes: List[str] = []
        cfg = getattr(self, "cfg", None) or SlTpGuardConfig()
        if not cfg.enabled or not positions:
            return notes

        client = getattr(exchange, "_client", None) or exchange
        now = time.time()

        for row in positions:
            sym = str(row.get("symbol", "") or "").upper()
            size = _f(row.get("size"))
            if not sym or size <= 0:
                continue

            origin = "bot" if sym in bot_symbols else "manual"
            if callable(origin_of):
                try:
                    origin = str(origin_of(sym) or origin)
                except Exception:
                    pass
            if not should_guard_origin(origin, cfg.include_manual):
                continue

            ex_sl, ex_tp = exchange_sl_tp(row)
            need_sl, need_tp = missing_sides(ex_sl, ex_tp)
            if not need_sl and not need_tp:
                continue

            if not self._due(sym, now):
                continue
            self._last_attempt[sym] = now

            entry = _f(row.get("avgPrice") or row.get("entryPrice") or row.get("markPrice"))
            side_raw = str(row.get("side", "") or "")
            side = "Buy" if side_raw.lower() in ("buy", "long") else "Sell"
            levels = bot_levels.get(sym, {}) if isinstance(bot_levels, dict) else {}
            target_sl, target_tp = compute_fallback_levels(
                side=side,
                entry=entry,
                bot_sl=_f(levels.get("stop_loss")),
                bot_tp=_f(levels.get("take_profit")),
                default_sl_pct=cfg.default_sl_pct,
                default_tp_pct=cfg.default_tp_pct,
                min_rr=cfg.min_rr,
            )

            missing_parts = []
            if need_sl:
                missing_parts.append("SL")
            if need_tp:
                missing_parts.append("TP")
            logger.warning(
                "%s: %s %s %s origin=%s entry=%.6f ex_sl=%s ex_tp=%s → target_sl=%.6f target_tp=%.6f",
                MISSING_MARKER,
                sym,
                side,
                "+".join(missing_parts),
                origin,
                entry,
                ex_sl if ex_sl > 0 else "",
                ex_tp if ex_tp > 0 else "",
                target_sl,
                target_tp,
            )

            position_idx = int(row.get("positionIdx", 0) or 0)
            repaired: List[str] = []
            errors: List[str] = []

            if need_sl and target_sl > 0 and hasattr(client, "update_stop_loss"):
                try:
                    res = await client.update_stop_loss(
                        sym, target_sl, position_idx=position_idx
                    )
                    if isinstance(res, dict) and res.get("success"):
                        repaired.append(f"SL={target_sl:.6g}")
                    else:
                        err = (
                            res.get("error")
                            if isinstance(res, dict)
                            else "update_stop_loss failed"
                        )
                        errors.append(f"SL:{err}")
                except Exception as exc:
                    errors.append(f"SL:{exc}")

            if need_tp and target_tp > 0 and hasattr(client, "update_take_profit"):
                try:
                    res = await client.update_take_profit(
                        sym, target_tp, position_idx=position_idx
                    )
                    if isinstance(res, dict) and res.get("success"):
                        repaired.append(f"TP={target_tp:.6g}")
                    else:
                        err = (
                            res.get("error")
                            if isinstance(res, dict)
                            else "update_take_profit failed"
                        )
                        errors.append(f"TP:{err}")
                except Exception as exc:
                    errors.append(f"TP:{exc}")

            if repaired:
                msg = f"🛡 {LOG_MARKER}: восстановлены {', '.join(repaired)} на {sym}"
                notes.append(msg)
                logger.info(
                    "%s: restored %s on %s %s (%s)",
                    LOG_MARKER,
                    ",".join(repaired),
                    sym,
                    side,
                    origin,
                )
            if errors:
                logger.error(
                    "%s: failed %s on %s: %s",
                    LOG_MARKER,
                    "+".join(missing_parts),
                    sym,
                    "; ".join(errors),
                )
                notes.append(f"⚠️ {LOG_MARKER}: не удалось поставить {sym}: {'; '.join(errors)}")

        return notes
