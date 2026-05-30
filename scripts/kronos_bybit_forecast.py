#!/usr/bin/env python3
"""
Kronos: прогноз BTC/ETH по свечам Bybit.

  python scripts/kronos_bybit_forecast.py
  python scripts/kronos_bybit_forecast.py --symbol ETHUSDT --telegram
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config
from prd_agent.integrations.kronos_forecast import forecast_symbol, format_telegram_summary
from prd_agent.integrations.telegram_credentials import resolve_telegram

log = logging.getLogger("kronos.forecast")


def send_telegram(cfg: dict, text: str) -> bool:
    import requests

    root = Path(cfg.get("_root") or ROOT)
    token, chat_id = resolve_telegram(cfg, root=root)
    if not token or not chat_id:
        log.error("Telegram не настроен")
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=25,
    )
    return bool(r.json().get("ok"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Kronos forecast BTC/ETH via Bybit")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--telegram", action="store_true", help="Отправить в Telegram")
    args = parser.parse_args()

    cfg = load_config(args.config if args.config.exists() else ROOT / "config.example.yaml")
    k = cfg.setdefault("kronos", {})
    if not k.get("kronos_home"):
        default_home = Path(__file__).resolve().parents[2] / "Kronos-master"
        if not default_home.is_dir():
            default_home = Path("/root/Kronos-master")
        k["kronos_home"] = str(default_home)

    symbols = args.symbols or k.get("symbols") or ["BTCUSDT", "ETHUSDT"]
    lines: list[str] = []

    for sym in symbols:
        log.info("Прогноз %s (модель %s)...", sym, k.get("model_id", "NeoQuasar/Kronos-mini"))
        res = forecast_symbol(sym, cfg)
        msg = format_telegram_summary(res).replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
        plain = (
            f"{res['symbol']}: {res['last_close']:.4f} -> {res['pred_close_end']:.4f} "
            f"({res['change_pct']:+.2f}% {res['direction']})"
        )
        print(plain)
        lines.append(format_telegram_summary(res))

    if args.telegram and lines:
        ok = send_telegram(cfg, "\n\n".join(lines))
        log.info("Telegram: %s", "OK" if ok else "FAIL")


if __name__ == "__main__":
    main()
