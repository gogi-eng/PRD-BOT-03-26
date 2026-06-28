"""RSS fetch + JSONL queue for AGENT-WORLD (phase B). Stdlib only."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping
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


def parse_event_published_utc(event: Mapping[str, Any]) -> datetime | None:
    """Дата публикации из RSS (published_hint), в UTC."""
    hint = str(event.get("published_hint", "") or "").strip()
    if not hint:
        return None
    try:
        dt = parsedate_to_datetime(hint)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    for candidate in (hint, hint.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def news_age_hours(event: Mapping[str, Any], *, now: datetime | None = None) -> float | None:
    """Возраст новости в часах от published_hint; None если дату не распознали."""
    pub = parse_event_published_utc(event)
    if pub is None:
        return None
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return max(0.0, (ref.astimezone(timezone.utc) - pub).total_seconds() / 3600.0)


def is_news_too_stale_for_trade(
    event: Mapping[str, Any],
    max_age_hours: float,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """max_age_hours <= 0 отключает проверку."""
    if max_age_hours <= 0:
        return False, ""
    age = news_age_hours(event, now=now)
    if age is None:
        return True, "не удалось определить дату публикации RSS (published_hint)"
    if age > max_age_hours:
        pub = parse_event_published_utc(event)
        pub_s = pub.isoformat() if pub else "?"
        return (
            True,
            f"новость устарела: возраст {age:.1f}ч > {max_age_hours:g}ч (published={pub_s})",
        )
    return False, ""


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
