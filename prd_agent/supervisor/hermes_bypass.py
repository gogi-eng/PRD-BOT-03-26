"""
Hermes bypass level 2 — пропуск «лёгких» отказов supervisor для сигналов TP-профиля.

Не обходит: panic/recovery, чёрный список символов, seed block_entry_utc_hours.
Может обойти: DEFENSIVE (часы preferred), learned_bad_hours supervisor.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple, TYPE_CHECKING

from prd_agent.entry.entry_soft_rules import compute_soft_score
from prd_agent.signals.types import UnifiedSignal
from prd_agent.time_hours import entry_check_hour, read_timezone_offset

if TYPE_CHECKING:
    from prd_agent.supervisor.supervisor_v4 import SupervisorV4


def _hermes_bypass_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    sup = cfg.get("supervisor_v4", {})
    if not isinstance(sup, dict):
        sup = {}
    link = sup.get("hermes_link", {})
    if not isinstance(link, dict):
        link = {}
    block = link.get("hermes_bypass", {})
    return block if isinstance(block, dict) else {}


def _atr_pct_percent(entry_context: Mapping[str, Any]) -> Optional[float]:
    if "atr_pct" in entry_context:
        try:
            return float(entry_context["atr_pct"])
        except (TypeError, ValueError):
            pass
    return None


def _volatility_normal(
    atr_pct: Optional[float], *, min_pct: float, max_pct: float
) -> bool:
    if atr_pct is None:
        return False
    return min_pct <= atr_pct <= max_pct


def bypassable_supervisor_denial(
    supervisor: "SupervisorV4",
    reason: str,
    *,
    check_hour: int,
) -> bool:
    """Можно ли вообще рассматривать Hermes bypass для этой причины отказа."""
    text = str(reason or "")
    if not text:
        return False
    low = text.lower()
    if "протокол восстановления" in low or "panic" in low:
        return False
    if "чёрном списке" in low or "черном списке" in low:
        return False
    if check_hour in supervisor._seed_blocked_hours:
        return False
    if "defensive" in low:
        return True
    if "заблокирован" in low and check_hour in supervisor._meta.learned_bad_hours:
        return True
    return False


def evaluate_hermes_bypass_level2(
    cfg: Dict[str, Any],
    supervisor: "SupervisorV4",
    sig: UnifiedSignal,
    entry_context: Mapping[str, Any],
    *,
    denied_reason: str,
    utc_hour: Optional[int] = None,
) -> Tuple[bool, str]:
    """Проверка Hermes TP-профиля для обхода лёгкого supervisor hold."""
    hb = _hermes_bypass_cfg(cfg)
    if not bool(hb.get("enabled", False)):
        return False, ""
    if int(hb.get("level", 0) or 0) < 2:
        return False, ""

    from datetime import datetime, timezone

    tz = read_timezone_offset(cfg)
    utc_hour = (
        utc_hour
        if utc_hour is not None
        else datetime.now(timezone.utc).hour
    )
    check_hour = entry_check_hour(utc_hour, tz)

    if not bypassable_supervisor_denial(
        supervisor, denied_reason, check_hour=check_hour
    ):
        return False, ""

    min_conf = float(hb.get("min_confidence", 0.92) or 0.92)
    if float(sig.confidence or 0) + 1e-9 < min_conf:
        return False, ""

    min_hour = int(hb.get("min_local_hour", 9) or 9)
    local_hour = int(entry_context.get("local_hour", check_hour)) % 24
    if local_hour < min_hour:
        return False, ""

    min_atr = float(hb.get("min_atr_pct", 0.288) or 0.288)
    max_atr = float(hb.get("max_atr_pct", 2.0) or 2.0)
    atr_pct = _atr_pct_percent(entry_context)
    if not _volatility_normal(atr_pct, min_pct=min_atr, max_pct=max_atr):
        return False, ""

    require_label = str(hb.get("require_soft_label", "favorable") or "favorable").lower()
    soft = compute_soft_score(
        entry_context,
        side=sig.side,
        cfg=cfg,
    )
    if soft.label.lower() != require_label:
        return False, ""

    short_reason = denied_reason.split(":", 1)[-1].strip()[:80]
    return (
        True,
        f"supervisor_v4: hermes_bypass L2 (было: {short_reason}; "
        f"conf={sig.confidence:.2f} soft={soft.label} hour={local_hour} atr={atr_pct:.3f}%)",
    )
