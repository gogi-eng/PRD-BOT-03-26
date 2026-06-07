#!/usr/bin/env python3
"""AGENT-WORLD phase B: RSS → JSONL queue for telegram_signal_agent (phase C consumer)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import yaml
except Exception:
    yaml = None  # type: ignore


def load_yaml(path: Path) -> dict:
    if yaml is None or not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch crypto RSS and append world_events.jsonl")
    ap.add_argument("--repo-dir", type=Path, default=ROOT)
    args = ap.parse_args()
    repo = args.repo_dir.resolve()
    cfg = load_yaml(repo / "config.yaml")
    raw = cfg.get("agent_world")
    if not isinstance(raw, dict) or not raw.get("enabled", False):
        print("[agent_world] disabled in config (agent_world.enabled)")
        return 0
    urls = list(raw.get("rss_urls") or [])
    if not urls:
        print("[agent_world] no rss_urls in config")
        return 1
    from telegram_agent.world_feed import WorldWriterState, append_queue, fetch_rss_items

    queue_path = repo / str(raw.get("queue_path", "reports/world/world_events.jsonl"))
    state_path = repo / str(raw.get("writer_state_path", "reports/world/agent_world_writer_state.json"))
    max_per = max(1, int(raw.get("max_items_per_feed", 15) or 15))
    st = WorldWriterState.load(state_path)
    seen = set(st.seen_ids)
    added = 0
    for url in urls:
        items = fetch_rss_items(str(url).strip(), max_items=max_per)
        for item in items:
            eid = str(item.get("id", ""))
            if not eid or eid in seen:
                continue
            append_queue(queue_path, item)
            seen.add(eid)
            added += 1
    st.seen_ids = list(seen)[-8000:]
    st.save(state_path)
    print(f"[agent_world] appended {added} new events → {queue_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
