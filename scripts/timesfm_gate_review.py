#!/usr/bin/env python3
"""Sunday TimesFM review: save vs cut-profit table from gate_state.json + bot.log."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def side_profitable(side: str, move_pct: float, eps: float = 0.02) -> bool:
    s = str(side or "").upper()
    if s in ("BUY", "LONG"):
        return move_pct > eps
    if s in ("SELL", "SHORT"):
        return move_pct < -eps
    return False


def side_losing(side: str, move_pct: float, eps: float = 0.02) -> bool:
    s = str(side or "").upper()
    if s in ("BUY", "LONG"):
        return move_pct < -eps
    if s in ("SELL", "SHORT"):
        return move_pct > eps
    return False


def tfm_mismatch(sample: Dict[str, Any]) -> bool:
    tfm = str(sample.get("tfm_direction") or "flat")
    sig = str(sample.get("signal_direction") or "flat")
    if tfm == "flat" or sig == "flat":
        return False
    return tfm != sig


def analyze_gate_state(state: Dict[str, Any]) -> Dict[str, Any]:
    saved: List[Dict[str, Any]] = []
    cut: List[Dict[str, Any]] = []
    neutral: List[Dict[str, Any]] = []
    by_sym: Dict[str, Dict[str, int]] = defaultdict(lambda: {"saved": 0, "cut": 0, "neutral": 0})

    for sym, data in (state.get("symbols") or {}).items():
        if not isinstance(data, dict):
            continue
        te = bool(data.get("trading_enabled"))
        for s in data.get("samples") or []:
            if not isinstance(s, dict) or not s.get("resolved"):
                continue
            move = float(s.get("actual_move_pct") or 0)
            side = str(s.get("side") or "")
            if not tfm_mismatch(s):
                continue
            row = {
                "symbol": sym,
                "side": side,
                "at": str(s.get("signal_at") or "")[:16],
                "move_pct": round(move, 2),
                "tfm": s.get("tfm_direction"),
                "filter_on": te,
            }
            if side_losing(side, move):
                saved.append(row)
                by_sym[sym]["saved"] += 1
            elif side_profitable(side, move):
                cut.append(row)
                by_sym[sym]["cut"] += 1
            else:
                neutral.append(row)
                by_sym[sym]["neutral"] += 1

    return {
        "saved": saved,
        "cut": cut,
        "neutral": neutral,
        "by_sym": dict(by_sym),
        "symbols": state.get("symbols") or {},
    }


def parse_log_blocks(bot_log: Path) -> List[Dict[str, str]]:
    pat = re.compile(
        r"TimesFM block (\w+) (\w+): timesfm: direction mismatch "
        r"\(tfm=(\w+) .* signal=(\w+)\)"
    )
    out: List[Dict[str, str]] = []
    if not bot_log.exists():
        return out
    for line in bot_log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.search(line)
        if m:
            out.append(
                {
                    "symbol": m.group(1),
                    "side": m.group(2),
                    "tfm": m.group(3),
                    "signal": m.group(4),
                    "ts": line[:19],
                }
            )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", type=Path, required=True)
    ap.add_argument("--log", type=Path, default=None)
    args = ap.parse_args()
    state = json.loads(args.gate.read_text(encoding="utf-8"))
    r = analyze_gate_state(state)
    blocks = parse_log_blocks(args.log) if args.log else []

    print("TIMESFM REVIEW")
    print(f"mismatch_saved={len(r['saved'])} mismatch_cut={len(r['cut'])} neutral={len(r['neutral'])}")
    print(f"log_blocks={len(blocks)}")
    print("\nBY_SYMBOL")
    for sym, d in sorted(r["by_sym"].items(), key=lambda x: -(x[1]["saved"] + x[1]["cut"])):
        st = r["symbols"].get(sym, {})
        print(
            f"{sym}\tfilter={st.get('trading_enabled')}\tacc={st.get('last_accuracy')}"
            f"\tsave={d['saved']}\tcut={d['cut']}\tneutral={d['neutral']}"
        )


if __name__ == "__main__":
    main()
