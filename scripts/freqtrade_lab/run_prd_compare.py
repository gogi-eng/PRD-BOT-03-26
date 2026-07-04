"""
Сравнение 3 наборов параметров Freqtrade с настройками PRD-BOT (dry-run backtest).
Запуск из корня PRD-BOT-ALL после setup_freqtrade_lab.ps1
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parent
FT = Path(r"C:\Temp\freqtrade-lab")
USER_DATA = FT / "user_data"
STRATEGY = "PrdMirrorStrategy"
TIMERANGE = "20250601-20250701"

# PRD-BOT ориентиры: SL ~2.5%, trailing ~1.2%, min_confidence аналог через RSI
SCENARIOS = [
    {"name": "prd_prod_like", "buy_rsi": 35, "sell_rsi": 65, "stoploss": -0.025},
    {"name": "prd_tighter_sl", "buy_rsi": 35, "sell_rsi": 65, "stoploss": -0.018},
    {"name": "prd_more_entries", "buy_rsi": 40, "sell_rsi": 60, "stoploss": -0.025},
]


def run_backtest(name: str, buy_rsi: int, sell_rsi: int, stoploss: float) -> dict:
    cmd = [
        str(FT / "venv" / "Scripts" / "freqtrade.exe"),
        "backtesting",
        "--config",
        str(USER_DATA / "config.json"),
        "--strategy",
        STRATEGY,
        "--strategy-path",
        str(USER_DATA / "strategies"),
        "--timerange",
        TIMERANGE,
        "--export",
        "none",
        "--cache",
        "day",
        "--eps",
        "--enable-protections",
    ]
    env = {
        **dict(__import__("os").environ),
        "FT_PARAM_BUY_RSI_MAX": str(buy_rsi),
        "FT_PARAM_SELL_RSI_MIN": str(sell_rsi),
    }
    # stoploss via strategy file default; hyperopt skipped for speed
    proc = subprocess.run(cmd, cwd=FT, capture_output=True, text=True, env=env)
    out = proc.stdout + proc.stderr
    result = {"scenario": name, "buy_rsi": buy_rsi, "sell_rsi": sell_rsi, "stoploss": stoploss, "ok": proc.returncode == 0}
    for line in out.splitlines():
        if "Total profit %" in line or "Tot Profit %" in line:
            result["profit_line"] = line.strip()
        if "Trades" in line and "Avg" in line:
            result["trades_line"] = line.strip()
        if "Max % of account underwater" in line or "Drawdown" in line:
            result["drawdown_line"] = line.strip()
    if not result.get("profit_line"):
        result["tail"] = "\n".join(out.splitlines()[-15:])
    return result


def main() -> int:
    if not (FT / "venv" / "Scripts" / "freqtrade.exe").exists():
        print("Сначала: powershell -File scripts/freqtrade_lab/setup_freqtrade_lab.ps1")
        return 1
    results = [run_backtest(**s) for s in SCENARIOS]
    out_path = LAB / "compare_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
