#!/usr/bin/env python3
"""
Демон: сигналы топ-трейдеров ai4trade → Telegram (только BTC/ETH).

  python scripts/ai4trade_telegram_notify.py          # цикл опроса
  python scripts/ai4trade_telegram_notify.py --once  # один проход (тест)

Конфиг: config.yaml → секция ai4trade_notify
Токен ai4trade: ai4trade.credentials.json (не в git)
Telegram: telegram.bot_token + chat_id из config / .env
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config
from prd_agent.telegram.resolve_credentials import resolve_telegram
from prd_agent.integrations.ai4trade_client import (
    fetch_following_feed,
    fetch_signal_detail,
    heartbeat,
    load_credentials,
)
from prd_agent.integrations.ai4trade_filter import (
    bases_from_signal,
    format_bases_label,
    matches_btc_eth,
)

log = logging.getLogger("ai4trade.telegram")
STATE_PATH = ROOT / "data" / "ai4trade_telegram_seen.json"
MAX_SEEN_IDS = 8000

HEARTBEAT_SIGNAL_TYPES = frozenset(
    {
        "signal",
        "strategy_published",
        "discussion_started",
        "discussion",
        "trade",
        "position",
        "realtime",
    }
)


def _esc(text: str) -> str:
    return html.escape(str(text or ""), quote=False)


def load_notify_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    n = dict(cfg.get("ai4trade_notify") or {})
    cred_rel = n.get("credentials_path") or "ai4trade.credentials.json"
    cred_path = Path(cred_rel)
    if not cred_path.is_absolute():
        cred_path = ROOT / cred_path
    n["_credentials_path"] = cred_path
    n.setdefault("enabled", True)
    n.setdefault("poll_interval_sec", 45)
    n.setdefault("feed_limit", 25)
    n.setdefault("symbols_allow", ["BTC", "ETH"])
    n.setdefault("cooldown_sec", 90)
    n.setdefault("state_path", str(STATE_PATH))
    return n


def load_seen_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"signal_ids": [], "last_keys": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"signal_ids": [], "last_keys": {}}


def save_seen_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ids: list[int] = state.get("signal_ids") or []
    if len(ids) > MAX_SEEN_IDS:
        state["signal_ids"] = ids[-MAX_SEEN_IDS:]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def already_seen(state: dict[str, Any], signal_id: int | None, dedupe_key: str) -> bool:
    if signal_id and signal_id in set(state.get("signal_ids") or []):
        return True
    last_keys: dict[str, float] = state.get("last_keys") or {}
    ts = last_keys.get(dedupe_key)
    return ts is not None


def mark_seen(
    state: dict[str, Any],
    signal_id: int | None,
    dedupe_key: str,
    *,
    cooldown_sec: int,
) -> None:
    ids: list[int] = list(state.get("signal_ids") or [])
    if signal_id and signal_id not in ids:
        ids.append(signal_id)
    state["signal_ids"] = ids
    last_keys: dict[str, float] = dict(state.get("last_keys") or {})
    now = time.time()
    last_keys[dedupe_key] = now
    # чистим старые ключи cooldown
    cutoff = now - max(cooldown_sec * 20, 3600)
    state["last_keys"] = {k: v for k, v in last_keys.items() if v >= cutoff}


def dedupe_key(signal: dict[str, Any], msg_type: str = "") -> str:
    sid = signal.get("id") or signal.get("signal_id")
    if sid:
        return f"id:{sid}"
    agent = signal.get("agent_id") or signal.get("agent_name") or "?"
    sym = signal.get("symbol") or ""
    title = (signal.get("title") or signal.get("content") or "")[:80]
    return f"{msg_type}:{agent}:{sym}:{hash(title)}"


def format_telegram_message(signal: dict[str, Any], *, msg_type: str = "") -> str:
    bases = bases_from_signal(signal)
    bases_s = format_bases_label(bases)
    agent = _esc(signal.get("agent_name") or signal.get("leader_name") or "?")
    sym = _esc(signal.get("symbol") or bases_s)
    side = _esc(signal.get("side") or "")
    typ = _esc(msg_type or signal.get("type") or "signal")
    title = _esc(signal.get("title") or "")
    content = _esc((signal.get("content") or "")[:900])
    entry = signal.get("entry_price")
    extra = ""
    if entry is not None:
        extra = f"\nвход: <code>{entry}</code>"
    if side:
        extra += f" | {side}"
    head = f"📡 <b>AI-Trader</b> [{bases_s}]"
    lines = [head, f"👤 {agent} · {sym} · <i>{typ}</i>{extra}"]
    if title:
        lines.append(f"<b>{title}</b>")
    if content:
        lines.append(content)
    return "\n".join(lines)


def send_telegram(cfg: dict[str, Any], text: str) -> bool:
    root = Path(cfg.get("_root") or ROOT)
    token, chat_id = resolve_telegram(cfg, root=root)
    if not token or not chat_id:
        log.error(
            "Telegram: нет bot_token/chat_id. Проверьте /root/PRD-BOT-ALL/.env "
            "(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID) или telegram: в config.yaml"
        )
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=25)
        data = r.json()
        if not data.get("ok"):
            log.warning("Telegram API: %s", data)
            return False
        return True
    except requests.RequestException as exc:
        log.warning("Telegram send failed: %s", exc)
        return False


def signal_from_heartbeat_message(msg: dict[str, Any]) -> dict[str, Any]:
    data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
    out: dict[str, Any] = {
        "id": data.get("signal_id") or msg.get("signal_id"),
        "agent_name": data.get("agent_name") or msg.get("agent_name"),
        "agent_id": data.get("agent_id") or msg.get("agent_id"),
        "symbol": data.get("symbol"),
        "symbols": data.get("symbols"),
        "side": data.get("side"),
        "type": msg.get("type"),
        "title": data.get("title") or msg.get("title"),
        "content": msg.get("content") or data.get("content"),
        "entry_price": data.get("entry_price"),
    }
    return out


def process_signal(
    cfg: dict[str, Any],
    notify: dict[str, Any],
    creds: dict[str, Any],
    state: dict[str, Any],
    signal: dict[str, Any],
    *,
    msg_type: str = "",
    fetch_detail: bool = False,
) -> bool:
    allowed = notify.get("symbols_allow") or ["BTC", "ETH"]
    if fetch_detail and signal.get("id") and not signal.get("symbol"):
        detail = fetch_signal_detail(creds, int(signal["id"]))
        if detail:
            signal = {**detail, **{k: v for k, v in signal.items() if v}}

    if not matches_btc_eth(signal, allowed_bases=allowed):
        return False

    sid = signal.get("id")
    if sid is not None:
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            sid = None
    key = dedupe_key(signal, msg_type)
    cooldown = int(notify.get("cooldown_sec") or 90)
    if already_seen(state, sid, key):
        return False

    text = format_telegram_message(signal, msg_type=msg_type)
    if not send_telegram(cfg, text):
        return False

    mark_seen(state, sid, key, cooldown_sec=cooldown)
    log.info(
        "Telegram OK: %s %s",
        signal.get("agent_name"),
        format_bases_label(bases_from_signal(signal)),
    )
    return True


def run_poll(
    cfg: dict[str, Any],
    notify: dict[str, Any],
    creds: dict[str, Any],
    state: dict[str, Any],
) -> int:
    sent = 0
    state_path = Path(notify["state_path"])

    try:
        hb = heartbeat(creds)
    except requests.RequestException as exc:
        log.warning("heartbeat: %s", exc)
        hb = {}

    for msg in hb.get("messages") or []:
        mtype = str(msg.get("type") or "")
        if mtype not in HEARTBEAT_SIGNAL_TYPES and "strategy" not in mtype and "discussion" not in mtype:
            continue
        sig = signal_from_heartbeat_message(msg)
        if process_signal(
            cfg, notify, creds, state, sig, msg_type=mtype, fetch_detail=True
        ):
            sent += 1

    try:
        feed = fetch_following_feed(creds, limit=int(notify.get("feed_limit") or 25))
    except requests.RequestException as exc:
        log.warning("feed: %s", exc)
        feed = []

    for sig in feed:
        if process_signal(cfg, notify, creds, state, sig, msg_type=str(sig.get("type") or "feed")):
            sent += 1

    save_seen_state(state_path, state)
    return sent


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="AI-Trader → Telegram (BTC/ETH)")
    parser.add_argument("--once", action="store_true", help="Один цикл и выход")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    root = Path(cfg.get("_root") or ROOT)
    tg_token, tg_chat = resolve_telegram(cfg, root=root)
    if not tg_token or not tg_chat:
        log.error(
            "Перед запуском задайте TELEGRAM_TOKEN и TELEGRAM_CHAT_ID в %s/.env "
            "(как у trading_bot)",
            root,
        )
        sys.exit(1)
    notify = load_notify_cfg(cfg)
    if not notify.get("enabled", True):
        log.info("ai4trade_notify.enabled=false — выход")
        return

    cred_path: Path = notify["_credentials_path"]
    if not cred_path.exists():
        log.error("Нет %s", cred_path)
        sys.exit(1)
    creds = load_credentials(cred_path)
    state_path = Path(notify["state_path"])
    state = load_seen_state(state_path)
    interval = int(notify.get("poll_interval_sec") or 45)

    log.info(
        "Старт: BTC/ETH → Telegram chat_id=%s…, интервал %s сек, ai4trade=%s",
        str(tg_chat)[:6],
        interval,
        creds.get("agent_name"),
    )

    while True:
        n = run_poll(cfg, notify, creds, state)
        if n:
            log.info("Отправлено сообщений: %s", n)
        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
