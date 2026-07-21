"""
Агент сопровождения открытых сделок в реальном времени (каждый цикл orchestrator).

Дополняет position_steward:
- перенос TP дальше, если сделка развивается в плюс;
- закрытие при откате от пика или развороте тренда;
- подтяжка SL при ослаблении импульса (не дублирует классический трейлинг).

Не открывает новые позиции.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from prd_agent.positions.exit_management import profit_pct
from prd_agent.positions.position_steward import PositionSteward, TrackedPosition
from prd_agent.positions.sr_sl_tp_adjust import adjust_sl_tp_with_sr_zones

logger = logging.getLogger("prd_agent.trade_companion")


def _side_norm(side: str) -> str:
    s = str(side or "").strip().upper()
    if s in ("LONG", "BUY"):
        return "Buy"
    if s in ("SHORT", "SELL"):
        return "Sell"
    return str(side or "").strip()


def _position_idx(row: Dict[str, Any]) -> int:
    for key in ("positionIdx", "position_idx"):
        try:
            return int(row.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _sma(closes: List[float], period: int) -> float:
    if not closes:
        return 0.0
    window = closes[-period:]
    if not window:
        return 0.0
    return sum(window) / len(window)


def trend_confirms(side: str, klines: List[Dict[str, Any]]) -> bool:
    """Импульс в сторону позиции: быстрая SMA выше/ниже медленной."""
    closes = [float(k.get("close", 0) or 0) for k in klines if float(k.get("close", 0) or 0) > 0]
    if len(closes) < 22:
        return False
    fast = _sma(closes, 8)
    slow = _sma(closes, 21)
    if fast <= 0 or slow <= 0:
        return False
    if _side_norm(side) == "Buy":
        return fast > slow * 1.001
    return fast < slow * 0.999


def trend_reversal_against(side: str, klines: List[Dict[str, Any]]) -> bool:
    """Разворот против позиции."""
    closes = [float(k.get("close", 0) or 0) for k in klines if float(k.get("close", 0) or 0) > 0]
    if len(closes) < 22:
        return False
    fast = _sma(closes, 8)
    slow = _sma(closes, 21)
    if fast <= 0 or slow <= 0:
        return False
    if _side_norm(side) == "Buy":
        return fast < slow * 0.999
    return fast > slow * 1.001


def progress_to_tp_pct(side: str, entry: float, price: float, take_profit: float) -> float:
    """Доля пути от входа к TP (0..100+)."""
    if entry <= 0 or take_profit <= 0 or price <= 0:
        return 0.0
    if _side_norm(side) == "Buy":
        total = take_profit - entry
        if total <= 0:
            return 0.0
        return (price - entry) / total * 100.0
    total = entry - take_profit
    if total <= 0:
        return 0.0
    return (entry - price) / total * 100.0


def tp_extension_improves(side: str, old_tp: float, new_tp: float) -> bool:
    if old_tp <= 0 or new_tp <= 0:
        return new_tp > 0 and new_tp != old_tp
    if _side_norm(side) == "Buy":
        return new_tp > old_tp * 1.001
    return new_tp < old_tp * 0.999


def sl_tightens(side: str, old_sl: float, new_sl: float, entry: float) -> bool:
    if new_sl <= 0:
        return False
    if old_sl <= 0:
        if _side_norm(side) == "Buy":
            return new_sl > entry * 0.999
        return new_sl < entry * 1.001
    if _side_norm(side) == "Buy":
        return new_sl > old_sl
    return new_sl < old_sl


@dataclass
class TradeCompanionConfig:
    enabled: bool = False
    bot_positions_only: bool = True
    kline_interval: str = "15"
    kline_limit: int = 80
    notify_telegram: bool = True
    action_cooldown_sec: float = 90.0

    extend_tp_enabled: bool = True
    extend_tp_min_profit_pct: float = 2.5
    extend_tp_min_progress_pct: float = 55.0
    extend_tp_require_trend: bool = True
    extend_tp_cooldown_sec: float = 300.0

    close_giveback_enabled: bool = True
    close_giveback_peak_min_pct: float = 2.0
    close_giveback_from_peak_pct: float = 45.0

    close_reversal_enabled: bool = True
    close_reversal_min_profit_pct: float = 0.3
    close_reversal_max_loss_pct: float = -1.5

    tighten_sl_on_weakness: bool = True
    tighten_sl_min_profit_pct: float = 1.2
    tighten_sl_to_breakeven_pct: float = 0.35

    @classmethod
    def from_cfg(cls, cfg: Dict[str, Any]) -> TradeCompanionConfig:
        raw = cfg.get("trade_companion")
        if not isinstance(raw, dict):
            raw = {}
        sr = cfg.get("execution_sr_zones") if isinstance(cfg.get("execution_sr_zones"), dict) else {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            bot_positions_only=bool(raw.get("bot_positions_only", True)),
            kline_interval=str(raw.get("kline_interval", "15")),
            kline_limit=int(raw.get("kline_limit", 80) or 80),
            notify_telegram=bool(raw.get("notify_telegram", True)),
            action_cooldown_sec=float(raw.get("action_cooldown_sec", 90) or 90),
            extend_tp_enabled=bool(raw.get("extend_tp_enabled", True)),
            extend_tp_min_profit_pct=float(raw.get("extend_tp_min_profit_pct", 2.5) or 2.5),
            extend_tp_min_progress_pct=float(raw.get("extend_tp_min_progress_pct", 55) or 55),
            extend_tp_require_trend=bool(raw.get("extend_tp_require_trend", True)),
            extend_tp_cooldown_sec=float(raw.get("extend_tp_cooldown_sec", 300) or 300),
            close_giveback_enabled=bool(raw.get("close_giveback_enabled", True)),
            close_giveback_peak_min_pct=float(raw.get("close_giveback_peak_min_pct", 2.0) or 2.0),
            close_giveback_from_peak_pct=float(raw.get("close_giveback_from_peak_pct", 45) or 45),
            close_reversal_enabled=bool(raw.get("close_reversal_enabled", True)),
            close_reversal_min_profit_pct=float(raw.get("close_reversal_min_profit_pct", 0.3) or 0.3),
            close_reversal_max_loss_pct=float(raw.get("close_reversal_max_loss_pct", -1.5) or -1.5),
            tighten_sl_on_weakness=bool(raw.get("tighten_sl_on_weakness", True)),
            tighten_sl_min_profit_pct=float(raw.get("tighten_sl_min_profit_pct", 1.2) or 1.2),
            tighten_sl_to_breakeven_pct=float(raw.get("tighten_sl_to_breakeven_pct", 0.35) or 0.35),
        )


@dataclass
class CompanionDecision:
    action: str
    reason: str
    new_tp: float = 0.0
    new_sl: float = 0.0


def evaluate_companion_actions(
    *,
    cfg: TradeCompanionConfig,
    side: str,
    entry: float,
    price: float,
    take_profit: float,
    stop_loss: float,
    peak_profit_pct: float,
    klines: List[Dict[str, Any]],
    sr_params: Dict[str, Any],
) -> Optional[CompanionDecision]:
    """Правила без I/O — для тестов и live-цикла."""
    p_pct = profit_pct(_side_norm(side), entry, price)

    if cfg.close_giveback_enabled and peak_profit_pct >= cfg.close_giveback_peak_min_pct:
        giveback = peak_profit_pct - p_pct
        if peak_profit_pct > 0 and giveback >= peak_profit_pct * (cfg.close_giveback_from_peak_pct / 100.0):
            return CompanionDecision(
                action="close",
                reason=(
                    f"откат {giveback:.2f}% от пика {peak_profit_pct:.2f}% "
                    f"(порог {cfg.close_giveback_from_peak_pct:.0f}%)"
                ),
            )

    if cfg.close_reversal_enabled and trend_reversal_against(side, klines):
        if p_pct <= cfg.close_reversal_max_loss_pct:
            return CompanionDecision(
                action="close",
                reason=f"разворот тренда при убытке {p_pct:.2f}%",
            )
        if 0 < p_pct < cfg.close_reversal_min_profit_pct:
            return CompanionDecision(
                action="close",
                reason=f"разворот тренда при слабой прибыли {p_pct:.2f}%",
            )

    if (
        cfg.extend_tp_enabled
        and take_profit > 0
        and p_pct >= cfg.extend_tp_min_profit_pct
    ):
        prog = progress_to_tp_pct(side, entry, price, take_profit)
        trend_ok = (not cfg.extend_tp_require_trend) or trend_confirms(side, klines)
        if prog >= cfg.extend_tp_min_progress_pct and trend_ok:
            _, new_tp, changed = adjust_sl_tp_with_sr_zones(
                entry=entry,
                side=side,
                stop_loss=stop_loss,
                take_profit=take_profit,
                klines=klines,
                sl_extra_atr=float(sr_params.get("sl_extra_atr", 0.1) or 0.1),
                tp_extra_atr=float(sr_params.get("tp_extra_atr", 0.08) or 0.08),
                preserve_min_rr=float(sr_params.get("preserve_min_rr", 2.0) or 2.0),
                sl_sr_level_index=int(sr_params.get("sl_sr_level_index", 1) or 1),
                min_tp_distance_pct=float(sr_params.get("min_tp_distance_pct", 1.0) or 1.0),
                prefer_far_tp=bool(sr_params.get("prefer_far_tp", True)),
                tp_sr_level_index=int(sr_params.get("tp_sr_level_index", 2) or 2),
                target_initial_tp_rr=float(sr_params.get("target_initial_tp_rr", 6.0) or 6.0),
            )
            if changed and tp_extension_improves(side, take_profit, new_tp):
                return CompanionDecision(
                    action="extend_tp",
                    reason=f"импульс +{p_pct:.2f}%, путь к TP {prog:.0f}%",
                    new_tp=new_tp,
                )

    if (
        cfg.tighten_sl_on_weakness
        and p_pct >= cfg.tighten_sl_min_profit_pct
        and trend_reversal_against(side, klines)
    ):
        be_buf = cfg.tighten_sl_to_breakeven_pct / 100.0
        if _side_norm(side) == "Buy":
            candidate = entry * (1.0 + be_buf)
        else:
            candidate = entry * (1.0 - be_buf)
        if sl_tightens(side, stop_loss, candidate, entry):
            return CompanionDecision(
                action="tighten_sl",
                reason=f"ослабление импульса при +{p_pct:.2f}%",
                new_sl=candidate,
            )

    return None


class TradeCompanionAgent:
    """Исполняющий слой сопровождения открытых позиций."""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self._companion_cfg = TradeCompanionConfig.from_cfg(cfg)
        self._sr_params = self._load_sr_params(cfg)
        self._last_action_at: Dict[str, float] = {}
        self._last_tp_extend_at: Dict[str, float] = {}

    @staticmethod
    def _load_sr_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
        sr = cfg.get("execution_sr_zones") if isinstance(cfg.get("execution_sr_zones"), dict) else {}
        return {
            "sl_extra_atr": sr.get("sl_extra_atr", 0.1),
            "tp_extra_atr": sr.get("tp_extra_atr", 0.08),
            "preserve_min_rr": sr.get("preserve_min_rr", 2.0),
            "sl_sr_level_index": sr.get("sl_sr_level_index", 1),
            "min_tp_distance_pct": sr.get("min_tp_distance_pct", 1.0),
            "prefer_far_tp": sr.get("prefer_far_tp", True),
            "tp_sr_level_index": sr.get("tp_sr_level_index", 2),
            "target_initial_tp_rr": sr.get("target_initial_tp_rr", 6.0),
        }

    def apply_config(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self._companion_cfg = TradeCompanionConfig.from_cfg(cfg)
        self._sr_params = self._load_sr_params(cfg)

    @property
    def enabled(self) -> bool:
        return self._companion_cfg.enabled

    def _cooldown_ok(self, sym: str, *, tp_extend: bool = False) -> bool:
        now = time.time()
        last = self._last_action_at.get(sym, 0.0)
        if now - last < self._companion_cfg.action_cooldown_sec:
            return False
        if tp_extend:
            last_tp = self._last_tp_extend_at.get(sym, 0.0)
            if now - last_tp < self._companion_cfg.extend_tp_cooldown_sec:
                return False
        return True

    def _mark_action(self, sym: str, *, tp_extend: bool = False) -> None:
        now = time.time()
        self._last_action_at[sym] = now
        if tp_extend:
            self._last_tp_extend_at[sym] = now

    async def manage_cycle(
        self,
        exchange: Any,
        positions: List[Dict[str, Any]],
        steward: PositionSteward,
    ) -> List[str]:
        """Вызывается каждый цикл orchestrator после position_steward.manage()."""
        cfg = self._companion_cfg
        if not cfg.enabled or not positions:
            return []

        notes: List[str] = []
        client = getattr(exchange, "_client", None)

        for sym, pos in list(steward._tracked.items()):
            if cfg.bot_positions_only and sym not in steward._bot_symbols:
                continue
            row = next(
                (p for p in positions if str(p.get("symbol", "")).upper() == sym),
                None,
            )
            if not row:
                continue

            price = float(row.get("markPrice") or pos.entry or 0)
            if price <= 0:
                continue

            klines = await exchange.get_klines(
                sym,
                interval=cfg.kline_interval,
                limit=cfg.kline_limit,
            )
            p_pct = profit_pct(pos.side, pos.entry, price)
            pos.peak_profit_pct = max(pos.peak_profit_pct, p_pct)

            decision = evaluate_companion_actions(
                cfg=cfg,
                side=pos.side,
                entry=pos.entry,
                price=price,
                take_profit=pos.take_profit,
                stop_loss=pos.stop_loss,
                peak_profit_pct=pos.peak_profit_pct,
                klines=klines or [],
                sr_params=self._sr_params,
            )
            if not decision:
                continue

            tp_extend = decision.action == "extend_tp"
            if not self._cooldown_ok(sym, tp_extend=tp_extend):
                continue

            if decision.action == "close":
                msg = await self._close_position(exchange, pos, decision.reason)
                if msg:
                    notes.append(msg)
                    self._mark_action(sym)
                    steward._tracked.pop(sym, None)
                continue

            if decision.action == "extend_tp" and client and hasattr(client, "update_take_profit"):
                res = await client.update_take_profit(
                    sym,
                    decision.new_tp,
                    position_idx=pos.position_idx,
                )
                if res.get("success"):
                    pos.take_profit = decision.new_tp
                    msg = (
                        f"🎯 Companion {sym}: TP→{decision.new_tp:.6g} "
                        f"({decision.reason})"
                    )
                    notes.append(msg)
                    logger.info(msg)
                    self._mark_action(sym, tp_extend=True)
                else:
                    logger.warning(
                        "Companion TP extend %s failed: %s",
                        sym,
                        res.get("error", ""),
                    )
                continue

            if decision.action == "tighten_sl" and client and hasattr(client, "update_stop_loss"):
                res = await client.update_stop_loss(
                    sym,
                    decision.new_sl,
                    position_idx=pos.position_idx,
                )
                if res.get("success"):
                    pos.stop_loss = decision.new_sl
                    pos.last_sl_sent = decision.new_sl
                    msg = (
                        f"🔒 Companion {sym}: SL→{decision.new_sl:.6g} "
                        f"({decision.reason})"
                    )
                    notes.append(msg)
                    logger.info(msg)
                    self._mark_action(sym)
                else:
                    logger.warning(
                        "Companion SL tighten %s failed: %s",
                        sym,
                        res.get("error", ""),
                    )

        return notes

    async def _close_position(
        self,
        exchange: Any,
        pos: TrackedPosition,
        reason: str,
    ) -> Optional[str]:
        if not hasattr(exchange, "close_position"):
            return None
        res = await exchange.close_position(
            pos.symbol,
            pos.side,
            qty=pos.qty,
            position_idx=pos.position_idx,
        )
        if res.get("success") or res.get("orderId"):
            msg = f"🤖 Companion выход {pos.symbol} ({reason})"
            logger.info("%s origin=%s", msg, pos.origin)
            return msg
        logger.warning(
            "Companion close failed %s: %s",
            pos.symbol,
            str(res.get("error", ""))[:120],
        )
        return None
