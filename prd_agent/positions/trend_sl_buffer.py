"""
Буфер стоп-лосса по тренду: SL чуть дальше от входа (защита от выноса стопов), TP при необходимости сдвигается для сохранения RR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from prd_agent.positions.sr_sl_tp_adjust import _rr_ratio, _simple_atr


@dataclass
class TrendSlBufferConfig:
    enabled: bool = True
    extra_sl_atr_mult: float = 0.35
    extra_sl_pct: float = 0.12
    max_extra_sl_atr_mult: float = 0.75
    preserve_min_rr: bool = True
    require_trend_alignment: bool = True
    use_klines_trend_fallback: bool = True
    klines_trend_ema_period: int = 20

    @classmethod
    def from_cfg(cls, cfg: Dict[str, Any]) -> TrendSlBufferConfig:
        raw = cfg.get("trend_sl_buffer")
        if not isinstance(raw, dict):
            raw = {}
        preserve = bool(raw.get("preserve_min_rr", True))
        min_rr = float(raw.get("min_rr_ratio", 0) or 0)
        if preserve and min_rr <= 0:
            q = cfg.get("quality_gate", {})
            if isinstance(q, dict):
                min_rr = float(q.get("min_rr_ratio", 2.0) or 2.0)
        return cls(
            enabled=bool(raw.get("enabled", True)),
            extra_sl_atr_mult=float(raw.get("extra_sl_atr_mult", 0.35) or 0.35),
            extra_sl_pct=float(raw.get("extra_sl_pct", 0.12) or 0.12),
            max_extra_sl_atr_mult=float(raw.get("max_extra_sl_atr_mult", 0.75) or 0.75),
            preserve_min_rr=preserve,
            require_trend_alignment=bool(raw.get("require_trend_alignment", True)),
            use_klines_trend_fallback=bool(raw.get("use_klines_trend_fallback", True)),
            klines_trend_ema_period=int(raw.get("klines_trend_ema_period", 20) or 20),
        )


def _normalize_side(side: str) -> str:
    s = str(side or "").strip().upper()
    if s in ("LONG",):
        return "BUY"
    if s in ("SHORT",):
        return "SELL"
    return s


def _htf_from_raw(raw: Optional[Dict[str, Any]]) -> int:
    if not isinstance(raw, dict):
        return 0
    for key in ("htf_4h_trend", "htf_trend"):
        if key in raw:
            try:
                return int(raw[key])
            except (TypeError, ValueError):
                pass
    nested = raw.get("metadata")
    if isinstance(nested, dict):
        return _htf_from_raw(nested)
    return 0


def _klines_trend_side(klines: List[dict], period: int = 20) -> int:
    """1 = up, -1 = down, 0 = неясно."""
    if len(klines) < period + 2:
        return 0
    closes = [float(k.get("close", 0) or 0) for k in klines if float(k.get("close", 0) or 0) > 0]
    if len(closes) < period + 2:
        return 0
    ema = sum(closes[-period:]) / period
    last = closes[-1]
    if last > ema * 1.001:
        return 1
    if last < ema * 0.999:
        return -1
    return 0


def is_with_trend(
    side: str,
    *,
    raw: Optional[Dict[str, Any]] = None,
    klines: Optional[List[dict]] = None,
    cfg: Optional[TrendSlBufferConfig] = None,
) -> bool:
    side_u = _normalize_side(side)
    if side_u not in ("BUY", "SELL"):
        return False
    htf = _htf_from_raw(raw)
    if htf > 0 and side_u == "BUY":
        return True
    if htf < 0 and side_u == "SELL":
        return True
    if htf != 0:
        return False
    if not cfg or not cfg.use_klines_trend_fallback or not klines:
        return not (cfg and cfg.require_trend_alignment)
    kt = _klines_trend_side(klines, cfg.klines_trend_ema_period)
    if kt > 0 and side_u == "BUY":
        return True
    if kt < 0 and side_u == "SELL":
        return True
    return not cfg.require_trend_alignment


def _extra_distance(entry: float, atr: float, cfg: TrendSlBufferConfig) -> float:
    by_pct = entry * max(0.0, cfg.extra_sl_pct) / 100.0 if entry > 0 else 0.0
    by_atr = atr * max(0.0, cfg.extra_sl_atr_mult)
    raw = max(by_pct, by_atr)
    cap = atr * max(0.0, cfg.max_extra_sl_atr_mult) if atr > 0 else entry * 0.015
    return min(raw, cap) if cap > 0 else raw


def apply_trend_sl_buffer(
    *,
    entry: float,
    side: str,
    stop_loss: float,
    take_profit: float,
    klines: List[dict],
    cfg: TrendSlBufferConfig,
    signal_raw: Optional[Dict[str, Any]] = None,
    preserve_min_rr: float = 0.0,
) -> Tuple[float, float, bool]:
    """
    Сдвигает SL дальше от входа по тренду. Возвращает (sl, tp, changed).
    """
    if not cfg.enabled or entry <= 0:
        return stop_loss, take_profit, False
    side_u = _normalize_side(side)
    side_exec = "Buy" if side_u == "BUY" else "Sell"
    sl = float(stop_loss or 0)
    tp = float(take_profit or 0)
    if sl <= 0:
        return stop_loss, take_profit, False

    if cfg.require_trend_alignment and not is_with_trend(
        side_u, raw=signal_raw, klines=klines, cfg=cfg
    ):
        return stop_loss, take_profit, False

    atr = _simple_atr(klines)
    if atr <= 0:
        atr = entry * 0.005
    extra = _extra_distance(entry, atr, cfg)
    if extra <= 0:
        return stop_loss, take_profit, False

    if side_u == "BUY":
        if sl >= entry:
            return stop_loss, take_profit, False
        new_sl = sl - extra
        if new_sl <= 0:
            return stop_loss, take_profit, False
    else:
        if sl <= entry:
            return stop_loss, take_profit, False
        new_sl = sl + extra

    rr_need = preserve_min_rr if preserve_min_rr > 0 else 0.0
    if rr_need <= 0 and cfg.preserve_min_rr:
        rr_need = 2.0

    new_tp = tp
    if tp > 0 and rr_need > 0:
        risk = abs(entry - new_sl)
        if risk > 0:
            if side_u == "BUY":
                min_tp = entry + risk * rr_need
                if tp <= entry or tp < min_tp:
                    new_tp = min_tp
            else:
                max_tp = entry - risk * rr_need
                if tp >= entry or tp > max_tp:
                    new_tp = max_tp

    if side_u == "BUY" and (new_sl >= entry or (new_tp > 0 and new_tp <= entry)):
        return stop_loss, take_profit, False
    if side_u == "SELL" and (new_sl <= entry or (new_tp > 0 and new_tp >= entry)):
        return stop_loss, take_profit, False

    tol = max(entry * 1e-8, 1e-10)
    if abs(new_sl - sl) < tol and abs(new_tp - tp) < tol:
        return stop_loss, take_profit, False

    if rr_need > 0 and _rr_ratio(entry, new_sl, new_tp, side_exec) + 1e-9 < rr_need:
        return stop_loss, take_profit, False

    return new_sl, new_tp, True
