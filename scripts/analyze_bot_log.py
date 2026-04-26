#!/usr/bin/env python3
"""
Разбор ``bot.log`` и опционально ``trade_history.json``:
- закрытия: причина, PnL, символ
- входы: сторона, grade (прокси «стратегии»)
- агрегаты: winrate, средний PnL, топ причин выхода

Запуск с корня проекта:
  python scripts/analyze_bot_log.py
  python scripts/analyze_bot_log.py --log path/to/bot.log --history trade_history.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Optional, Tuple


CLOSED_RE = re.compile(
    r"CLOSED\s+(\w+)\s*:\s*pnl=\$([-\d.]+)\s*reason=(.+?)(?:\s*$)",
    re.IGNORECASE,
)
ENTERED_RE = re.compile(
    r"ENTERED\s+(\w+)\s*:\s*(\w+)\s*\[(\w+)\]",
    re.IGNORECASE,
)
SCALP_RE = re.compile(
    r"SCALP\s+SIGNAL\s+(\w+)\s*:\s*(\w+)\s+conf=",
    re.IGNORECASE,
)


def parse_log_lines(text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    closed: List[Dict[str, Any]] = []
    entered: List[Dict[str, Any]] = []
    scalp: List[Dict[str, Any]] = []
    for line in text.splitlines():
        m = CLOSED_RE.search(line)
        if m:
            closed.append(
                {
                    "symbol": m.group(1).upper(),
                    "pnl": float(m.group(2)),
                    "reason": m.group(3).strip(),
                }
            )
            continue
        m = ENTERED_RE.search(line)
        if m:
            entered.append(
                {
                    "symbol": m.group(1).upper(),
                    "side": m.group(2).upper(),
                    "grade": m.group(3).upper(),
                }
            )
            continue
        m = SCALP_RE.search(line)
        if m:
            scalp.append({"symbol": m.group(1).upper(), "side": m.group(2).upper(), "strategy": "scalp_session"})
    return closed, entered, scalp


def load_history(path: Optional[Path]) -> List[Dict[str, Any]]:
    if not path or not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def summarize_closed(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    wins = sum(1 for r in rows if r["pnl"] > 0)
    by_reason: DefaultDict[str, int] = defaultdict(int)
    pnl_by_reason: DefaultDict[str, float] = defaultdict(float)
    by_sym: DefaultDict[str, List[float]] = defaultdict(list)
    for r in rows:
        reason = r["reason"].split(":")[0].strip()
        by_reason[reason] += 1
        pnl_by_reason[reason] += r["pnl"]
        by_sym[r["symbol"]].append(r["pnl"])
    return {
        "n": len(rows),
        "winrate": wins / len(rows),
        "total_pnl": sum(r["pnl"] for r in rows),
        "avg_pnl": sum(r["pnl"] for r in rows) / len(rows),
        "by_exit_reason_count": dict(sorted(by_reason.items(), key=lambda x: -x[1])),
        "by_exit_reason_pnl": dict(sorted(pnl_by_reason.items(), key=lambda x: -abs(x[1]))),
        "by_symbol_trades": {s: len(v) for s, v in sorted(by_sym.items(), key=lambda x: -len(x[1]))},
        "by_symbol_total_pnl": {s: sum(v) for s, v in by_sym.items()},
    }


def summarize_entries(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_grade: DefaultDict[str, int] = defaultdict(int)
    by_side: DefaultDict[str, int] = defaultdict(int)
    for r in rows:
        by_grade[r.get("grade", "?")] += 1
        by_side[r.get("side", "?")] += 1
    return {"n": len(rows), "by_grade": dict(by_grade), "by_side": dict(by_side)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze PRD-SCALP bot logs")
    ap.add_argument("--log", type=Path, default=Path("bot.log"))
    ap.add_argument("--history", type=Path, default=Path("trade_history.json"))
    args = ap.parse_args()

    report: Dict[str, Any] = {"log_path": str(args.log), "history_path": str(args.history)}

    if args.log.exists():
        text = args.log.read_text(encoding="utf-8", errors="replace")
        closed, entered, scalp = parse_log_lines(text)
        report["from_log"] = {
            "closed": summarize_closed(closed),
            "entered": summarize_entries(entered),
            "scalp_signals": len(scalp),
        }
    else:
        report["from_log"] = {"error": "log file not found"}

    hist = load_history(args.history)
    if hist:
        pnl = [float(x.get("pnl", 0) or 0) for x in hist]
        strat: DefaultDict[str, int] = defaultdict(int)
        reasons: DefaultDict[str, int] = defaultdict(int)
        for x in hist:
            strat[str(x.get("strategy", "unknown"))] += 1
            reasons[str(x.get("reason", "")).split(":")[0]] += 1
        report["from_trade_history"] = {
            "n": len(hist),
            "winrate": sum(1 for p in pnl if p > 0) / max(1, len(pnl)),
            "total_pnl": sum(pnl),
            "by_strategy_count": dict(strat),
            "by_reason_head": dict(sorted(reasons.items(), key=lambda x: -x[1])[:20]),
        }

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
