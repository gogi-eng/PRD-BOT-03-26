#!/usr/bin/env python3
"""
Следить за топ-трейдерами на ai4trade.ai: подписка + heartbeat + лента сигналов.

Использование:
  python scripts/ai4trade_watch_top_traders.py follow          # подписаться на топ-N
  python scripts/ai4trade_watch_top_traders.py list             # кого уже следим
  python scripts/ai4trade_watch_top_traders.py top              # показать топ без подписки
  python scripts/ai4trade_watch_top_traders.py watch          # опрос heartbeat (Ctrl+C)
  python scripts/ai4trade_watch_top_traders.py feed           # разовая лента sort=active
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CREDS_PATH = ROOT / "ai4trade.credentials.json"
STATE_PATH = ROOT / "data" / "ai4trade_watch_state.json"
BASE_URL = "https://ai4trade.ai/api"
DEFAULT_TOP_N = 8
MIN_POSITION_PNL = 0.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ai4trade.watch")


def load_creds() -> dict:
    if not CREDS_PATH.exists():
        log.error("Нет файла %s — сначала зарегистрируйте агента.", CREDS_PATH)
        sys.exit(1)
    return json.loads(CREDS_PATH.read_text(encoding="utf-8"))


def headers(creds: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {creds['token']}"}


def fetch_grouped(creds: dict, limit: int = 50) -> list[dict]:
    r = requests.get(
        f"{BASE_URL}/signals/grouped",
        params={"limit": limit},
        headers=headers(creds),
        timeout=90,
    )
    r.raise_for_status()
    return r.json().get("agents") or []


def rank_traders(agents: list[dict], my_agent_id: int) -> list[dict]:
    """Сортировка: position_pnl, затем signal_count."""
    filtered = [a for a in agents if int(a.get("agent_id", 0)) != my_agent_id]
    filtered = [
        a
        for a in filtered
        if float(a.get("position_pnl") or a.get("total_pnl") or 0) >= MIN_POSITION_PNL
    ]
    filtered.sort(
        key=lambda a: (
            float(a.get("position_pnl") or a.get("total_pnl") or 0),
            int(a.get("signal_count") or 0),
        ),
        reverse=True,
    )
    return filtered


def cmd_top(creds: dict, top_n: int) -> None:
    agents = rank_traders(fetch_grouped(creds), int(creds.get("agent_id", 0)))
    print(f"Топ-{min(top_n, len(agents))} трейдеров (по position_pnl):\n")
    for i, a in enumerate(agents[:top_n], 1):
        pnl = a.get("position_pnl", a.get("total_pnl", 0))
        print(
            f"  {i:2}. [{a['agent_id']}] {a['agent_name']}"
            f"  pnl={pnl:.2f}  signals={a.get('signal_count', 0)}"
        )


def follow_leader(creds: dict, leader_id: int) -> dict:
    r = requests.post(
        f"{BASE_URL}/signals/follow",
        json={"leader_id": leader_id},
        headers=headers(creds),
        timeout=30,
    )
    if r.status_code >= 400:
        return {"success": False, "leader_id": leader_id, "error": r.text}
    return r.json()


def cmd_follow(creds: dict, top_n: int) -> None:
    my_id = int(creds.get("agent_id", 0))
    agents = rank_traders(fetch_grouped(creds), my_id)[:top_n]
    if not agents:
        log.error("Нет трейдеров для подписки.")
        return

    followed: list[dict] = []
    for a in agents:
        lid = int(a["agent_id"])
        name = a.get("agent_name", str(lid))
        resp = follow_leader(creds, lid)
        ok = resp.get("success", False)
        if ok:
            log.info("Подписка OK: %s (%s)", name, lid)
            followed.append(
                {
                    "leader_id": lid,
                    "leader_name": name,
                    "position_pnl": a.get("position_pnl"),
                }
            )
        else:
            err = resp.get("error") or resp
            # уже подписан — не критично
            if "already" in str(err).lower():
                log.info("Уже подписан: %s (%s)", name, lid)
                followed.append({"leader_id": lid, "leader_name": name})
            else:
                log.warning("Не удалось подписаться на %s (%s): %s", name, lid, err)

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "followed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "top_n": top_n,
        "leaders": followed,
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Сохранено: %s (%d лидеров)", STATE_PATH, len(followed))


def cmd_list(creds: dict) -> None:
    r = requests.get(
        f"{BASE_URL}/signals/following",
        headers=headers(creds),
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    subs = data.get("following") or data.get("subscriptions") or []
    if not subs:
        print("Подписок нет. Запустите: python scripts/ai4trade_watch_top_traders.py follow")
        return
    print(f"Подписки ({len(subs)}):\n")
    for s in subs:
        print(
            f"  - [{s.get('leader_id')}] {s.get('leader_name')}"
            f"  подписчиков={s.get('follower_count', '?')}"
            f"  сделок_7д={s.get('recent_trade_count_7d', '?')}"
        )
        if s.get("latest_strategy_title"):
            print(f"      стратегия: {s['latest_strategy_title'][:70]}")


def cmd_feed(creds: dict, limit: int = 20) -> None:
    r = requests.get(
        f"{BASE_URL}/signals/feed",
        params={"limit": limit, "sort": "active"},
        headers=headers(creds),
        timeout=30,
    )
    r.raise_for_status()
    signals = r.json().get("signals") or []
    print(f"Активная лента ({len(signals)} сигналов):\n")
    for s in signals:
        print(
            f"  [{s.get('agent_name')}] {s.get('symbol')} {s.get('side')} "
            f"type={s.get('type')} — {(s.get('content') or '')[:80]}"
        )


def process_heartbeat(data: dict) -> None:
    for msg in data.get("messages") or []:
        log.info("MSG %s: %s", msg.get("type"), msg.get("content"))
    for task in data.get("tasks") or []:
        log.info("TASK %s", task.get("type") or task)


def cmd_watch(creds: dict, interval: int) -> None:
    agent_id = int(creds.get("agent_id", 0))
    log.info(
        "Heartbeat каждые %s сек (agent_id=%s). Ctrl+C — остановка.",
        interval,
        agent_id,
    )
    while True:
        try:
            r = requests.post(
                f"{BASE_URL}/claw/agents/heartbeat",
                json={"agent_id": agent_id, "status": "alive"},
                headers=headers(creds),
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if (data.get("message_count") or 0) + (data.get("task_count") or 0) > 0:
                process_heartbeat(data)
            rec = data.get("recommended_poll_interval_seconds") or interval
            interval = int(rec)
        except KeyboardInterrupt:
            log.info("Остановлено пользователем.")
            break
        except requests.RequestException as exc:
            log.warning("Heartbeat ошибка: %s", exc)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-Trader: следить за топ-трейдерами")
    parser.add_argument(
        "command",
        choices=["follow", "list", "top", "watch", "feed"],
        help="follow=подписаться, list=подписки, top=рейтинг, watch=heartbeat, feed=лента",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Сколько лидеров")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Интервал heartbeat (сек)",
    )
    args = parser.parse_args()
    creds = load_creds()

    if args.command == "top":
        cmd_top(creds, args.top_n)
    elif args.command == "follow":
        cmd_follow(creds, args.top_n)
    elif args.command == "list":
        cmd_list(creds)
    elif args.command == "feed":
        cmd_feed(creds)
    elif args.command == "watch":
        cmd_watch(creds, args.interval)


if __name__ == "__main__":
    main()
