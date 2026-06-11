"""Импульс → ретест → подтверждение (3 свечи) перед входом."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _candle_body(candle: Dict) -> float:
    return abs(float(candle.get("close", 0) or 0) - float(candle.get("open", 0) or 0))


def _candle_dir(candle: Dict) -> int:
    o = float(candle.get("open", 0) or 0)
    c = float(candle.get("close", 0) or 0)
    if c > o:
        return 1
    if c < o:
        return -1
    return 0


def _entry_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("entry", {})
    return raw if isinstance(raw, dict) else {}


def check_impulse_retest_confirmation(
    *,
    side: str,
    klines: List[Dict],
    atr_value: float,
    confidence: float,
    cfg: Dict[str, Any],
) -> Tuple[bool, str]:
    ec = _entry_cfg(cfg)
    ze = cfg.get("zone_entry", {}) if isinstance(cfg.get("zone_entry"), dict) else {}
    enabled = bool(ze.get("require_impulse_retest", ec.get("impulse_retest_confirmation_enabled", True)))
    if not enabled:
        return True, ""

    if atr_value <= 0 or len(klines) < 3:
        return True, ""

    bypass = float(
        ze.get(
            "impulse_retest_bypass_confidence",
            ec.get("impulse_confirm_conf_bypass", 0.92),
        )
    )
    if confidence >= bypass:
        return True, "impulse_retest: bypass high confidence"

    side_u = str(side or "").strip().upper()
    if side_u in ("LONG",):
        side_u = "BUY"
    elif side_u in ("SHORT",):
        side_u = "SELL"
    if side_u not in ("BUY", "SELL"):
        return True, ""

    direction = 1 if side_u == "BUY" else -1
    min_impulse_body = atr_value * max(
        0.0, float(ec.get("impulse_min_body_atr", 0.45))
    )
    max_retest_body = atr_value * max(
        0.0,
        float(
            ze.get(
                "retest_max_body_atr",
                ec.get("retest_max_body_ratio", 0.35),
            )
        ),
    )
    min_confirm_body = atr_value * max(
        0.0, float(ec.get("confirm_min_body_ratio", 0.0))
    )

    impulse = klines[-3]
    retest = klines[-2]
    confirm = klines[-1]

    impulse_body = _candle_body(impulse)
    impulse_dir = _candle_dir(impulse)
    retest_body = _candle_body(retest)
    retest_dir = _candle_dir(retest)
    confirm_body = _candle_body(confirm)
    confirm_dir = _candle_dir(confirm)

    if impulse_dir != direction or impulse_body < min_impulse_body:
        return False, (
            "impulse_retest: нет импульса пробоя "
            f"(body={impulse_body:.4f} < {min_impulse_body:.4f})"
        )

    if retest_dir != -direction:
        return False, "impulse_retest: нет свечи ретеста уровня"

    if retest_body > max_retest_body:
        return False, (
            "impulse_retest: ретест слишком глубокий "
            f"(body={retest_body:.4f} > {max_retest_body:.4f})"
        )

    if confirm_dir != direction:
        return False, "impulse_retest: нет свечи подтверждения"

    if min_confirm_body > 0 and confirm_body < min_confirm_body:
        return False, (
            "impulse_retest: слабое подтверждение "
            f"(body={confirm_body:.4f} < {min_confirm_body:.4f})"
        )

    return True, "impulse_retest: пробой → ретест → подтверждение OK"
