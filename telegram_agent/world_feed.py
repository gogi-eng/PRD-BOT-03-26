"""RSS fetch + JSONL queue for AGENT-WORLD (phase B). Stdlib only."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError


def _strip_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    parts = [t.strip() for t in el.itertext() if t and str(t).strip()]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def fetch_rss_items(url: str, *, max_items: int = 20, timeout_sec: float = 25.0) -> list[dict[str, Any]]:
    req = urllib.request.Request(url, headers={"User-Agent": "PRD-BOT-AgentWorld/1.0 (RSS)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
    except URLError:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    items_out: list[dict[str, Any]] = []

    def handle_channel(channel: ET.Element) -> None:
        for child in channel:
            if _strip_tag(child.tag).lower() != "item":
                continue
            title_el = link_el = desc_el = pub_el = None
            for sub in child:
                t = _strip_tag(sub.tag).lower()
                if t == "title":
                    title_el = sub
                elif t == "link":
                    link_el = sub
                elif t in {"description", "summary", "content", "encoded"}:
                    desc_el = sub
                elif t in {"pubdate", "published", "updated"}:
                    pub_el = sub
            title = _text(title_el)
            link = _text(link_el).strip() or url
            summary = _text(desc_el)
            if len(summary) > 4000:
                summary = summary[:3997] + "…"
            pub = _text(pub_el)
            uid_src = f"{link}|{title}".encode("utf-8", errors="ignore")
            eid = hashlib.sha256(uid_src).hexdigest()[:20]
            items_out.append(
                {
                    "id": eid,
                    "source_url": url,
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published_hint": pub,
                    "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            if len(items_out) >= max_items:
                return

    root_tag = _strip_tag(root.tag).lower()
    if root_tag == "rss":
        ch = root.find(".//{*}channel") or root.find("channel")
        if ch is not None:
            handle_channel(ch)
        else:
            for el in root.iter():
                if _strip_tag(el.tag).lower() == "channel":
                    handle_channel(el)
                    break
    elif root_tag in {"feed", "rdf"}:
        for child in root:
            if _strip_tag(child.tag).lower() in {"entry", "item"}:
                title = link = summary = pub = ""
                for sub in child:
                    st = _strip_tag(sub.tag).lower()
                    if st == "title":
                        title = _text(sub)
                    elif st == "link":
                        href = sub.attrib.get("href")
                        link = href or _text(sub)
                    elif st in {"summary", "content"}:
                        summary = _text(sub) or summary
                    elif st in {"updated", "published", "issued"}:
                        pub = _text(sub) or pub
                uid_src = f"{link}|{title}".encode("utf-8", errors="ignore")
                eid = hashlib.sha256(uid_src).hexdigest()[:20]
                items_out.append(
                    {
                        "id": eid,
                        "source_url": url,
                        "title": title,
                        "link": link.strip() or url,
                        "summary": summary[:4000],
                        "published_hint": pub,
                        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                    }
                )
                if len(items_out) >= max_items:
                    break

    return items_out[:max_items]


def append_queue(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


@dataclass
class WorldWriterState:
    seen_ids: list[str]

    @classmethod
    def load(cls, path: Path) -> WorldWriterState:
        if not path.exists():
            return cls(seen_ids=[])
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            ids = raw.get("seen_ids", [])
            if isinstance(ids, list):
                return cls(seen_ids=[str(x) for x in ids][-8000:])
        except Exception:
            pass
        return cls(seen_ids=[])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seen_ids": self.seen_ids[-8000:]}, indent=2), encoding="utf-8")
