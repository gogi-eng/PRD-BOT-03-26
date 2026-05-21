#!/usr/bin/env python3
"""
Собирает config.yaml на сервере в один правильный вид:
- одна секция telegram_signal_agent (из config.telegram_agent.snippet.yaml)
- одна секция signals (без дублей, путь signals_inbox.jsonl)
Запуск: cd ~/PRD-BOT-ALL && ./venv/bin/python3 scripts/fix_server_config.py
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Нужен PyYAML: ./venv/bin/pip install pyyaml")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config.yaml"
SNIPPET = ROOT / "config.telegram_agent.snippet.yaml"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def main() -> int:
    if not CFG.exists():
        print(f"Нет файла {CFG}")
        return 1
    if not SNIPPET.exists():
        print(f"Нет файла {SNIPPET} — сделайте git pull origin 21.05.26-ALL")
        return 1

    raw = CFG.read_text(encoding="utf-8")
    dup_tg = raw.count("telegram_signal_agent:")
    dup_sig = raw.count("\nsignals:") + (1 if raw.strip().startswith("signals:") else 0)
    if dup_tg > 1:
        print(f"⚠ В файле {dup_tg} раза встречается telegram_signal_agent: — будет оставлена одна (из snippet)")
    if dup_sig > 1:
        print(f"⚠ В файле несколько блоков signals: — будут объединены в один")

    data = _load(CFG)
    snippet = _load(SNIPPET)
    tsa = snippet.get("telegram_signal_agent")
    if not isinstance(tsa, dict):
        print("В snippet нет telegram_signal_agent")
        return 1

    # Сохранить старые поля signals, если были в первом блоке (PyYAML уже мог их потерять)
    old_sig = data.get("signals") if isinstance(data.get("signals"), dict) else {}
    signals = {
        "own_agents_enabled": old_sig.get("own_agents_enabled", True),
        "telegram_inbox_enabled": True,
        "telegram_signals_jsonl": "reports/telegram_signals/signals_inbox.jsonl",
        "telegram_inbox_only_approved": True,
        "telegram_inbox_backlog_lines": int(old_sig.get("telegram_inbox_backlog_lines", 100)),
        "min_telegram_confidence": float(old_sig.get("min_telegram_confidence", 0.55)),
        "whale_news_enabled": old_sig.get("whale_news_enabled", True),
    }
    for key in ("prd_repo_path", "telegram_channels_enabled"):
        if key in old_sig:
            signals[key] = old_sig[key]

    data["telegram_signal_agent"] = tsa
    data["signals"] = signals

    bak = CFG.with_name(f"config.yaml.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(CFG, bak)
    CFG.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"✓ Резервная копия: {bak.name}")
    print("✓ config.yaml исправлен")
    print("")
    print("Проверка:")
    print(f"  telegram_signal_agent.inbox_jsonl = {tsa.get('inbox_jsonl')}")
    print(f"  signals.telegram_signals_jsonl   = {signals['telegram_signals_jsonl']}")
    print(f"  audit_jsonl_enabled              = {tsa.get('audit_jsonl_enabled')}")
    print("")
    print("Дальше:")
    print("  sudo systemctl restart telegram_signal_agent")
    print("  sudo systemctl restart trading_bot")
    print("  ./venv/bin/python3 scripts/diagnose_trading.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
