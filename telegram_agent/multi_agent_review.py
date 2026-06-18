"""
Second opinion: три «анalysta» (technical, sentiment, risk) голосуют без открытия сделок.
Вдохновлено TradingAgents; rule-based, без лишних LLM-вызовов.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from telegram_agent.signal_quality import (
    rule_based_review,
    signal_levels_plausible,
    structure_score,
)

_BULL_WORDS = re.compile(
    r"\b(pump|breakout|long|buy|лонг|покуп|bull|рост|moon|🚀|📈)\b",
    re.I,
)
_BEAR_WORDS = re.compile(
    r"\b(dump|breakdown|short|sell|шорт|продаж|bear|паден|crash|🔻|📉)\b",
    re.I,
)


def _technical_vote(parsed: Dict[str, Any], market_regime: str) -> Dict[str, Any]:
    sc = structure_score(parsed)
    ok_lv, lv_reason = signal_levels_plausible(parsed)
    side = str(parsed.get("side") or "").upper()
    vote = sc >= 70 and ok_lv and side in {"BUY", "SELL"}
    if market_regime == "chop" and sc < 78:
        vote = False
    confidence = min(100, max(0, sc))
    reason = "structure_ok" if vote else (lv_reason or "weak_structure")
    return {"role": "technical", "vote": vote, "confidence": confidence, "reason": reason}


def _sentiment_vote(parsed: Dict[str, Any], raw_text: str, market_regime: str) -> Dict[str, Any]:
    side = str(parsed.get("side") or "").upper()
    text = raw_text or ""
    bull = len(_BULL_WORDS.findall(text))
    bear = len(_BEAR_WORDS.findall(text))
    regime_bias = {"trend_up": 1, "trend_down": -1, "chop": 0, "unknown": 0}.get(
        str(market_regime or "unknown"), 0
    )
    if side == "BUY":
        score = 55 + min(25, bull * 8) - min(20, bear * 10) + regime_bias * 10
    elif side == "SELL":
        score = 55 + min(25, bear * 8) - min(20, bull * 10) - regime_bias * 10
    else:
        score = 40
    vote = score >= 58
    if market_regime == "chop":
        vote = vote and score >= 65
    return {
        "role": "sentiment",
        "vote": vote,
        "confidence": min(100, max(0, int(score))),
        "reason": f"regime={market_regime} bull={bull} bear={bear}",
    }


def _risk_vote(parsed: Dict[str, Any]) -> Dict[str, Any]:
    entry = float(parsed.get("entry") or 0)
    sl = float(parsed.get("stop_loss") or 0)
    tp = float(parsed.get("take_profit") or 0)
    side = str(parsed.get("side") or "").upper()
    if entry <= 0 or sl <= 0 or tp <= 0 or side not in {"BUY", "SELL"}:
        return {"role": "risk", "vote": False, "confidence": 20, "reason": "missing_levels"}
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return {"role": "risk", "vote": False, "confidence": 15, "reason": "zero_risk"}
    rr = reward / risk
    sl_pct = risk / entry * 100
    vote = rr >= 1.5 and sl_pct <= 8.0
    conf = int(min(100, 40 + rr * 15))
    if sl_pct > 12:
        vote = False
        conf = max(10, conf - 25)
    return {
        "role": "risk",
        "vote": vote,
        "confidence": conf,
        "reason": f"RR={rr:.2f} SL%={sl_pct:.2f}",
    }


def multi_agent_consensus(
    parsed: Dict[str, Any],
    *,
    market_regime: str = "unknown",
    raw_text: str = "",
    min_votes: int = 2,
) -> Dict[str, Any]:
    """Консенсус 3 аналитиков. Не открывает сделки — только оценка."""
    votes: List[Dict[str, Any]] = [
        _technical_vote(parsed, market_regime),
        _sentiment_vote(parsed, raw_text, market_regime),
        _risk_vote(parsed),
    ]
    yes = sum(1 for v in votes if v["vote"])
    avg_conf = sum(int(v["confidence"]) for v in votes) // max(1, len(votes))
    approve = yes >= min_votes
    stance = "bull" if yes >= 2 else ("bear" if yes == 0 else "mixed")
    return {
        "approve": approve,
        "confidence": avg_conf,
        "reason": f"multi_agent_{yes}/3_{stance}",
        "analysts": votes,
        "consensus_yes": yes,
        "consensus_total": len(votes),
        "stance": stance,
    }


def merge_review_with_consensus(
    base: Dict[str, Any],
    consensus: Dict[str, Any],
    *,
    advisory_only: bool = True,
    block_on_bear_majority: bool = False,
) -> Dict[str, Any]:
    """Слить rule_based/openrouter с multi-agent. По умолчанию — только пометка в reason."""
    out = dict(base)
    yes = int(consensus.get("consensus_yes", 0))
    stance = str(consensus.get("stance", "mixed"))
    ma_conf = int(consensus.get("confidence", 0))
    base_conf = int(out.get("confidence", 0) or 0)
    analyst_note = f" | MA:{yes}/3 {stance}({ma_conf})"
    out["reason"] = str(out.get("reason", ""))[:240] + analyst_note
    out["multi_agent"] = consensus
    if advisory_only and not block_on_bear_majority:
        if yes >= 2 and base.get("approve"):
            out["confidence"] = min(100, base_conf + 2)
        elif yes == 0 and base.get("approve"):
            out["confidence"] = max(0, base_conf - 3)
        return out
    if block_on_bear_majority and yes == 0:
        out["approve"] = False
        out["confidence"] = min(base_conf, ma_conf)
        out["reason"] = f"blocked_by_multi_agent_bear{analyst_note}"
    elif yes >= 2:
        out["approve"] = bool(out.get("approve")) and bool(consensus.get("approve"))
        out["confidence"] = (base_conf + ma_conf) // 2
    return out
