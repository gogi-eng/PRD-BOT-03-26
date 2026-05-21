#!/usr/bin/env python3
"""
Убирает из анализа Telegram-каналы без торговых сигналов дольше N часов (по умолчанию 24ч).

Данные: state telegram_signal_agent (channel_activity) + signals_inbox.jsonl.

Запуск на сервере:
  ./venv/bin/python3 scripts/prune_inactive_telegram_channels.py --dry-run
  ./venv/bin/python3 scripts/prune_inactive_telegram_channels.py --apply

Cron (раз в 6 часов):
  0 */6 * * * cd /root/PRD-BOT-ALL && ./venv/bin/python3 scripts/prune_inactive_telegram_channels.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import yaml
except ImportError:
    print("Нужен PyYAML в venv")
    raise SystemExit(1)

from telegram_agent.channel_prune import (  # noqa: E402
    apply_prune_to_config,
    find_inactive_channels,
    load_channel_activity,
    merge_ratings_into_activity,
    normalize_chat_name,
    prune_config_from_agent,
    scan_jsonl_last_signals,
)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _notify_telegram(lines: list[str]) -> None:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        import urllib.parse
        import urllib.request

        text = "\n".join(lines)[:3800]
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
        ).encode()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST"), timeout=15)
    except Exception as exc:
        print(f"Telegram notify failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Отключить неактивные Telegram-каналы")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, не менять config")
    parser.add_argument("--apply", action="store_true", help="Обновить config.yaml и state")
    parser.add_argument("--hours", type=float, default=None, help="Порог без сигналов (часы)")
    args = parser.parse_args()
    if not args.dry_run and not args.apply:
        args.dry_run = True

    cfg_path = ROOT / "config.yaml"
    data = _load_yaml(cfg_path)
    tsa = data.get("telegram_signal_agent", {})
    if not isinstance(tsa, dict):
        print("Нет секции telegram_signal_agent в config.yaml")
        return 1

    pcfg = prune_config_from_agent(tsa)
    if args.hours is not None:
        pcfg.inactive_hours = float(args.hours)
    if not pcfg.enabled:
        print("channel_prune.enabled=false — выход")
        return 0

    state_path = ROOT / str(tsa.get("state_path", "telegram_signal_agent_state.json"))
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    if not isinstance(state, dict):
        state = {}

    activity = load_channel_activity(state)
    merge_ratings_into_activity(state, activity)

    out_dir = ROOT / str(tsa.get("out_dir", "reports/telegram_signals"))
    inbox_rel = str(tsa.get("inbox_jsonl", "reports/telegram_signals/signals_inbox.jsonl"))
    inbox = (ROOT / inbox_rel).resolve()
    audit = out_dir / "signals.jsonl"
    scan_jsonl_last_signals([Path(inbox), Path(audit)], activity)

    trusted = {normalize_chat_name(x) for x in (tsa.get("trusted_signal_sources", []) or [])}
    ignored_raw = list(tsa.get("ignored_chats", []) or [])
    already_ignored = {normalize_chat_name(x) for x in ignored_raw}

    inactive = find_inactive_channels(
        activity,
        pcfg,
        trusted=trusted,
        already_ignored=already_ignored,
    )

    print(f"=== Неактивные каналы (>{pcfg.inactive_hours:.0f}ч без сигнала) ===")
    if not inactive:
        print("Нет каналов для отключения.")
        return 0

    for src, key, reason in inactive:
        row = activity.get(key, {})
        print(f"  - {src}")
        print(f"      {reason} | last_signal={row.get('last_signal_at', '—')}")

    if args.dry_run:
        print("\nРежим dry-run: config не изменён. Запустите с --apply для отключения.")
        return 0

    added = apply_prune_to_config(data, inactive)
    now = datetime.now(timezone.utc).isoformat()
    for src, key, reason in inactive:
        if normalize_chat_name(src) in {normalize_chat_name(a) for a in added}:
            slot = activity.setdefault(key, {"source": src})
            slot["pruned_at"] = now
            slot["prune_reason"] = reason
    state["channel_activity"] = activity

    bak = cfg_path.with_name(f"config.yaml.bak.prune.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(cfg_path, bak)
    cfg_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    log_path = out_dir / "channel_prune.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        for src, _k, reason in inactive:
            f.write(
                json.dumps(
                    {"at": now, "channel": src, "reason": reason, "added_to_ignored": src in added},
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(f"\n✓ Добавлено в ignored_chats: {len(added)}")
    for name in added:
        print(f"    + {name}")
    print(f"✓ Резервная копия config: {bak.name}")
    print(f"✓ Лог: {log_path}")

    if pcfg.notify_telegram and added:
        _notify_telegram(
            ["PRD-BOT: отключены неактивные каналы (нет сигналов 24ч+)", ""]
            + [f"• {x}" for x in added[:25]]
        )

    print("\nПерезапустите коллектор:")
    print("  sudo systemctl restart telegram_signal_agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
