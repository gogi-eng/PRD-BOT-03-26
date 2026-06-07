"""Порог уверенности и антиспам повторов по symbol+side."""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Tuple, Union

from prd_agent.signals.pump_dump_mode import is_pump_dump_signal
from prd_agent.signals.types import UnifiedSignal

_SCORE_RE = re.compile(r"multi-agent\s+score\s*=\s*([+-]?[0-9.]+)", re.I)


def normalize_confidence(conf: float) -> float:
    """0..1; значения >1 трактуются как проценты 0..100."""
    c = float(conf or 0)
    if c > 1.0:
        return min(1.0, c / 100.0)
    return max(0.0, c)


def meets_threshold(conf: float, threshold: float) -> bool:
    return normalize_confidence(conf) >= float(threshold)


def load_min_analysis_confidence(cfg: Dict[str, Any]) -> float:
    sig = cfg.get("signals", {}) if isinstance(cfg.get("signals"), dict) else {}
    rep = cfg.get("reporter", {}) if isinstance(cfg.get("reporter"), dict) else {}
    return float(
        sig.get(
            "min_analysis_confidence",
            rep.get("high_confidence_threshold", 0.80),
        )
    )


def filter_signal_dicts_by_threshold(
    signals: List[Dict[str, Any]], threshold: float
) -> List[Dict[str, Any]]:
    return [
        s
        for s in signals
        if meets_threshold(float(s.get("confidence", 0) or 0), threshold)
    ]


def load_signal_notify_cooldown_sec(cfg: Dict[str, Any]) -> float:
    sig = cfg.get("signals", {}) if isinstance(cfg.get("signals"), dict) else {}
    return float(sig.get("signal_notify_cooldown_sec", 900))


def load_min_multi_agent_score(cfg: Dict[str, Any]) -> float:
    sig = cfg.get("signals", {}) if isinstance(cfg.get("signals"), dict) else {}
    t = cfg.get("trading", {}) if isinstance(cfg.get("trading"), dict) else {}
    return float(sig.get("min_multi_agent_score", t.get("min_multi_agent_score", 0.4)))


def extract_multi_agent_score(sig: Union[UnifiedSignal, Dict[str, Any]]) -> float:
    raw: Dict[str, Any] = {}
    reason = ""
    if isinstance(sig, dict):
        raw = sig.get("raw") if isinstance(sig.get("raw"), dict) else {}
        reason = str(sig.get("reason", "") or "")
    else:
        raw = sig.raw if isinstance(sig.raw, dict) else {}
        reason = str(sig.reason or "")

    if "multi_agent_score" in raw:
        return abs(float(raw.get("multi_agent_score", 0) or 0))
    agg = raw.get("aggregate")
    if agg is not None:
        return abs(float(agg))
    m = _SCORE_RE.search(reason)
    if m:
        return abs(float(m.group(1)))
    return 0.0


def passes_emit_gate(sig: Union[UnifiedSignal, Dict[str, Any]], cfg: Dict[str, Any]) -> bool:
    """
    Telegram и вход в сделку:
    - conf >= min_analysis_confidence (по умолчанию 90%), или
    - own_multi_agent / hybrid с score >= min_multi_agent_score (0.4).
  TA/telegram/whale — только по conf >= 90%.
    """
    if isinstance(sig, dict):
        conf = float(sig.get("confidence", 0) or 0)
        source = str(sig.get("source", "") or "")
        reason = str(sig.get("reason", "") or "")
        raw = sig.get("raw") if isinstance(sig.get("raw"), dict) else {}
        probe: Union[UnifiedSignal, Dict[str, Any]] = {
            "confidence": conf,
            "source": source,
            "reason": reason,
            "raw": raw,
        }
    else:
        conf = sig.confidence
        source = sig.source
        probe = sig

    min_conf = load_min_analysis_confidence(cfg)
    min_score = load_min_multi_agent_score(cfg)

    if isinstance(sig, UnifiedSignal):
        pump_probe = sig
    else:
        pump_probe = UnifiedSignal(
            symbol=str(sig.get("symbol", "")),
            side=str(sig.get("side", "")),
            confidence=conf,
            source=source,
            reason=reason,
            raw=raw,
        )
    if is_pump_dump_signal(pump_probe):
        pd_min = float((cfg.get("pump_dump_trade") or {}).get("min_confidence", 0.65))
        if meets_threshold(conf, pd_min):
            return True

    sig_cfg = cfg.get("signals", {}) if isinstance(cfg.get("signals"), dict) else {}
    min_tg = float(sig_cfg.get("min_telegram_confidence", 0.65))
    src = source.lower()
    if src in ("telegram", "agent_world") and meets_threshold(conf, min_tg):
        return True

    if meets_threshold(conf, min_conf):
        return True

    if src == "own_multi_agent":
        return extract_multi_agent_score(probe) > min_score
    if src == "hybrid":
        raw_h = probe.get("raw", {}) if isinstance(probe, dict) else probe.raw
        sources = raw_h.get("sources", []) if isinstance(raw_h, dict) else []
        if "own_multi_agent" in sources:
            return extract_multi_agent_score(probe) > min_score
    return False


def filter_signal_dicts(signals: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in signals if passes_emit_gate(s, cfg)]


class PerSymbolSignalCooldown:
    """Не чаще одного раза на пару symbol+side за cooldown_sec (Telegram и цикл)."""

    def __init__(self, cooldown_sec: float = 900.0):
        self.cooldown_sec = max(60.0, float(cooldown_sec))
        self._last_at: Dict[Tuple[str, str], float] = {}

    def is_on_cooldown(self, symbol: str, side: str) -> bool:
        key = (symbol.upper(), side.strip())
        last = self._last_at.get(key, 0.0)
        return (time.time() - last) < self.cooldown_sec

    def mark_handled(self, symbol: str, side: str) -> None:
        self._last_at[(symbol.upper(), side.strip())] = time.time()

    def remaining_sec(self, symbol: str, side: str) -> int:
        key = (symbol.upper(), side.strip())
        last = self._last_at.get(key, 0.0)
        left = self.cooldown_sec - (time.time() - last)
        return max(0, int(left))
