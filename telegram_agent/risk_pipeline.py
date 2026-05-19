"""Pre-OpenRouter and pre-execute risk gates for Telegram-sourced signals."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class SignalLike(Protocol):
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    leverage: int
    parser_confidence: int


@dataclass
class RiskPipelineConfig:
    enabled: bool = True
    # Reward/risk from parsed/post-filled SL/TP (0 = do not enforce).
    min_rr: float = 0.0
    # SL distance from entry as percent of entry (0 = off).
    max_sl_distance_pct: float = 0.0
    min_sl_distance_pct: float = 0.0
    # TP distance from entry as percent of entry (0 = off).
    max_tp_distance_pct: float = 0.0
    min_tp_distance_pct: float = 0.0
    # LONG: SL < entry < TP; SHORT: TP < entry < SL
    enforce_geometry: bool = False
    # Virtual channel score 0..100; only applied to auto_execute path (0 = off).
    min_channel_rating_for_auto: float = 0.0
    # (ask-bid)/mid * 100; 0 = skip spread check.
    max_spread_pct: float = 0.0
    # Optional: block auto if post text confidence is below (0 = off). Mirrors strict mode idea.
    min_parser_confidence_for_auto: int = 0


def _pct_distance(a: float, b: float) -> float:
    mid = abs(float(a) + float(b)) / 2.0
    if mid <= 0:
        return 0.0
    return abs(float(a) - float(b)) / mid * 100.0


def compute_rr(side: str, entry: float, sl: float, tp: float) -> float:
    side_u = str(side or "").upper()
    entry_f = float(entry or 0.0)
    sl_f = float(sl or 0.0)
    tp_f = float(tp or 0.0)
    if entry_f <= 0 or sl_f <= 0 or tp_f <= 0:
        return 0.0
    if side_u == "BUY":
        risk = entry_f - sl_f
        reward = tp_f - entry_f
    elif side_u == "SELL":
        risk = sl_f - entry_f
        reward = entry_f - tp_f
    else:
        return 0.0
    if risk <= 0 or reward <= 0:
        return 0.0
    return reward / risk


def geometry_ok(side: str, entry: float, sl: float, tp: float) -> tuple[bool, str]:
    side_u = str(side or "").upper()
    e, slf, tpf = float(entry or 0.0), float(sl or 0.0), float(tp or 0.0)
    if e <= 0 or slf <= 0 or tpf <= 0:
        return False, "missing или нулевые уровни"
    if side_u == "BUY":
        if not (slf < e < tpf):
            return False, f"геометрия LONG: нужно SL<{e:g}<TP, сейчас SL={slf:g} TP={tpf:g}"
    elif side_u == "SELL":
        if not (tpf < e < slf):
            return False, f"геометрия SHORT: нужно TP<{e:g}<SL, сейчас TP={tpf:g} SL={slf:g}"
    else:
        return False, f"неизвестная сторона: {side_u}"
    return True, ""


class RiskPipeline:
    def __init__(self, cfg: RiskPipelineConfig):
        self.cfg = cfg

    @classmethod
    def from_agent_cfg(cls, node: dict[str, Any] | None) -> RiskPipeline:
        raw = node if isinstance(node, dict) else {}
        cfg = RiskPipelineConfig(
            enabled=bool(raw.get("enabled", True)),
            min_rr=float(raw.get("min_rr", 0.0) or 0.0),
            max_sl_distance_pct=float(raw.get("max_sl_distance_pct", 0.0) or 0.0),
            min_sl_distance_pct=float(raw.get("min_sl_distance_pct", 0.0) or 0.0),
            max_tp_distance_pct=float(raw.get("max_tp_distance_pct", 0.0) or 0.0),
            min_tp_distance_pct=float(raw.get("min_tp_distance_pct", 0.0) or 0.0),
            enforce_geometry=bool(raw.get("enforce_geometry", False)),
            min_channel_rating_for_auto=float(raw.get("min_channel_rating_for_auto", 0.0) or 0.0),
            max_spread_pct=float(raw.get("max_spread_pct", 0.0) or 0.0),
            min_parser_confidence_for_auto=int(raw.get("min_parser_confidence_for_auto", 0) or 0),
        )
        return cls(cfg)

    def pre_openrouter(self, sig: SignalLike) -> tuple[bool, str]:
        """Cheap checks before OpenRouter (save tokens/latency)."""
        if not self.cfg.enabled:
            return True, ""
        side = str(sig.side or "").upper()
        entry, sl, tp = float(sig.entry or 0.0), float(sig.stop_loss or 0.0), float(sig.take_profit or 0.0)
        if side not in {"BUY", "SELL"} or entry <= 0 or sl <= 0 or tp <= 0:
            return True, ""
        if self.cfg.enforce_geometry:
            ok, msg = geometry_ok(side, entry, sl, tp)
            if not ok:
                return False, msg
        if self.cfg.min_sl_distance_pct > 0:
            d = _pct_distance(entry, sl)
            if d + 1e-9 < self.cfg.min_sl_distance_pct:
                return False, f"SL слишком близко к входу: {d:.3f}% < min {self.cfg.min_sl_distance_pct}%"
        if self.cfg.max_sl_distance_pct > 0:
            d = _pct_distance(entry, sl)
            if d > self.cfg.max_sl_distance_pct + 1e-9:
                return False, f"SL слишком далеко: {d:.3f}% > max {self.cfg.max_sl_distance_pct}%"
        if self.cfg.min_tp_distance_pct > 0:
            d = _pct_distance(entry, tp)
            if d + 1e-9 < self.cfg.min_tp_distance_pct:
                return False, f"TP слишком близко к входу: {d:.3f}% < min {self.cfg.min_tp_distance_pct}%"
        if self.cfg.max_tp_distance_pct > 0:
            d = _pct_distance(entry, tp)
            if d > self.cfg.max_tp_distance_pct + 1e-9:
                return False, f"TP слишком далеко: {d:.3f}% > max {self.cfg.max_tp_distance_pct}%"
        if self.cfg.min_rr > 0:
            rr = compute_rr(side, entry, sl, tp)
            if rr + 1e-9 < self.cfg.min_rr:
                return False, f"RR={rr:.2f} < min {self.cfg.min_rr}"
        return True, ""

    def pre_execute_auto(self, sig: SignalLike, *, channel_score: float, trusted_source: bool) -> tuple[bool, str]:
        """Stricter gates right before placing an order."""
        if not self.cfg.enabled:
            return True, ""
        if trusted_source:
            return True, ""
        if self.cfg.min_parser_confidence_for_auto > 0:
            pc = int(getattr(sig, "parser_confidence", 0) or 0)
            if pc < self.cfg.min_parser_confidence_for_auto:
                return (
                    False,
                    f"parser_confidence={pc}% < min_auto { self.cfg.min_parser_confidence_for_auto}%",
                )
        if self.cfg.min_channel_rating_for_auto > 0:
            if float(channel_score or 0.0) + 1e-9 < self.cfg.min_channel_rating_for_auto:
                return (
                    False,
                    f"рейтинг канала={channel_score:.1f} < min_auto {self.cfg.min_channel_rating_for_auto}",
                )
        return True, ""

    def check_spread(self, bid: float, ask: float) -> tuple[bool, str]:
        if self.cfg.max_spread_pct <= 0:
            return True, ""
        b, a = float(bid or 0.0), float(ask or 0.0)
        if b <= 0 or a <= 0 or a < b:
            return False, "стакан пуст или некорректен"
        mid = (a + b) / 2.0
        if mid <= 0:
            return False, "mid<=0"
        spread_pct = (a - b) / mid * 100.0
        if spread_pct > self.cfg.max_spread_pct + 1e-9:
            return False, f"спред {spread_pct:.3f}% > max {self.cfg.max_spread_pct}%"
        return True, ""
