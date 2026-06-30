#!/usr/bin/env python3
"""
Синхронизация чатов Cursor (agent-transcripts) в репозиторий.

Источник (локально на каждом ПК):
  ~/.cursor/projects/<workspace-slug>/agent-transcripts/<uuid>/<uuid>.jsonl

Назначение:
  .cursor/chats/archive/<uuid>.md
  .cursor/chats/INDEX.md
  .cursor/chats/LAST_HANDOFF.md

Примеры:
  python scripts/sync_cursor_chats.py
  python scripts/sync_cursor_chats.py --push
  python scripts/sync_cursor_chats.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
CHATS_DIR = ROOT / ".cursor" / "chats"
ARCHIVE_DIR = CHATS_DIR / "archive"
STATE_FILE = CHATS_DIR / ".sync_state.json"

USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE)
SKIP_USER_PREFIXES = (
    "<open_and_recently_viewed_files>",
    "<git_status>",
    "<agent_transcripts>",
    "<agent_skills>",
    "<mcp_file_system>",
    "<user_info>",
    "<rules>",
)


def _run_git(args: List[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git_remote_slug(repo_root: Path) -> str:
    proc = _run_git(["remote", "get-url", "origin"], cwd=repo_root)
    if proc.returncode != 0:
        return ""
    url = (proc.stdout or "").strip().lower()
    url = re.sub(r"\.git$", "", url)
    url = url.split(":")[-1].split("/")[-1]
    return re.sub(r"[^a-z0-9]+", "-", url).strip("-")


def _repo_match_tokens(repo_root: Path) -> List[str]:
    tokens: List[str] = []
    name = repo_root.name.lower()

    def add(value: str) -> None:
        v = value.strip().lower()
        if len(v) >= 4 and v not in tokens:
            tokens.append(v)

    add(name)
    add(name.replace(".", "-"))
    add(name.replace("_", "-"))
    for part in re.split(r"[-_.]+", name):
        add(part)
    remote = _git_remote_slug(repo_root)
    if remote:
        add(remote)
        for part in remote.split("-"):
            add(part)
    for marker in ("prd-bot", "prd-bot-all", "agent-world"):
        if marker.replace("-", "") in name.replace("-", "").replace("_", ""):
            add(marker)
    return tokens


def _project_matches(project_dir: Path, tokens: Iterable[str]) -> bool:
    slug = project_dir.name.lower()
    for token in tokens:
        if len(token) >= 6 and token in slug:
            return True
        compact = token.replace("-", "")
        if len(compact) >= 8 and compact in slug.replace("-", ""):
            return True
    return "prd-bot" in slug or "agent-world" in slug


def _cursor_projects_dir() -> Path:
    return Path.home() / ".cursor" / "projects"


def _find_transcript_files(repo_root: Path) -> List[Path]:
    projects_dir = _cursor_projects_dir()
    if not projects_dir.is_dir():
        return []

    tokens = _repo_match_tokens(repo_root)
    files: List[Path] = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        if not _project_matches(project_dir, tokens):
            continue
        transcripts = project_dir / "agent-transcripts"
        if not transcripts.is_dir():
            continue
        for jsonl in transcripts.glob("*/*.jsonl"):
            files.append(jsonl)
    return sorted(set(files))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_user_text(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    m = USER_QUERY_RE.search(text)
    if m:
        return m.group(1).strip()
    for prefix in SKIP_USER_PREFIXES:
        if text.startswith(prefix):
            return ""
    return text


def _clean_assistant_text(raw: str) -> str:
    text = raw.strip()
    if not text or text == "[REDACTED]":
        return ""
    text = text.replace("[REDACTED]", "").strip()
    return text


def _message_text(role: str, content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        raw = str(item.get("text") or "")
        if role == "user":
            cleaned = _clean_user_text(raw)
        else:
            cleaned = _clean_assistant_text(raw)
        if cleaned:
            parts.append(cleaned)
    return "\n\n".join(parts).strip()


def _title_from_messages(messages: List[Tuple[str, str]]) -> str:
    for role, text in messages:
        if role == "user" and text:
            one_line = re.sub(r"\s+", " ", text).strip()
            return one_line[:120]
    return "cursor-chat"


def _slugify(value: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "-", value.strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    return (slug[:max_len] or "chat").lower()


def _parse_jsonl(path: Path) -> List[Tuple[str, str, str]]:
    rows: List[Tuple[str, str, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = str(row.get("role") or "unknown")
            msg = row.get("message") or {}
            text = _message_text(role, msg.get("content"))
            if text:
                rows.append((role, text, f"L{line_no}"))
    return rows


def _format_markdown(
    *,
    chat_id: str,
    source: Path,
    project_slug: str,
    messages: List[Tuple[str, str, str]],
    exported_at: str,
    host: str,
) -> str:
    title = _title_from_messages([(r, t) for r, t, _ in messages])
    lines = [
        f"# {title}",
        "",
        f"- **id:** `{chat_id}`",
        f"- **exported:** {exported_at}",
        f"- **host:** {host}",
        f"- **project:** `{project_slug}`",
        f"- **source:** `{source}`",
        "",
        "---",
        "",
    ]
    for role, text, _ in messages:
        header = "Пользователь" if role == "user" else "Ассистент"
        lines.append(f"## {header}")
        lines.append("")
        lines.append(text)
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_state() -> Dict[str, Any]:
    if not STATE_FILE.is_file():
        return {"chats": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"chats": {}}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_index(entries: List[Dict[str, str]]) -> None:
    lines = [
        "# Cursor chats (автосинхронизация)",
        "",
        "Эти файлы создаёт `scripts/sync_cursor_chats.py`.",
        "На другом ПК: `git pull`, затем читайте `.cursor/chats/archive/`.",
        "",
        f"Обновлено: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "| Дата | ID | Заголовок | Файл |",
        "|------|----|-----------|------|",
    ]
    for item in sorted(entries, key=lambda x: x.get("mtime", ""), reverse=True):
        lines.append(
            f"| {item.get('date', '')} | `{item.get('id', '')[:8]}` | "
            f"{item.get('title', '')[:80]} | [{item.get('file', '')}](archive/{item.get('file', '')}) |"
        )
    (CHATS_DIR / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_handoff(entries: List[Dict[str, str]], latest_md: Optional[Path]) -> None:
    lines = [
        "# Последний чат (handoff)",
        "",
        "Краткая шпаргалка для продолжения на другом компьютере.",
        "",
        f"Обновлено: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    if not entries:
        lines.append("Пока нет экспортированных чатов.")
    else:
        top = sorted(entries, key=lambda x: x.get("mtime", ""), reverse=True)[0]
        lines.extend(
            [
                f"- **Чат:** `{top.get('id', '')}`",
                f"- **Заголовок:** {top.get('title', '')}",
                f"- **Файл:** `.cursor/chats/archive/{top.get('file', '')}`",
                "",
                "На домашнем/рабочем ПК:",
                "",
                "```bash",
                "git pull",
                "# откройте INDEX.md и нужный файл в archive/",
                "```",
                "",
            ]
        )
        if latest_md and latest_md.is_file():
            excerpt = latest_md.read_text(encoding="utf-8", errors="replace")
            excerpt = excerpt[:4000]
            lines.extend(["## Фрагмент последнего чата", "", excerpt])
    (CHATS_DIR / "LAST_HANDOFF.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def sync_chats(repo_root: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    chats_state: Dict[str, Any] = dict(state.get("chats") or {})

    host = ""
    try:
        host = socket.gethostname()
    except OSError:
        host = "unknown"

    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    transcript_files = _find_transcript_files(repo_root)
    index_entries: List[Dict[str, str]] = []
    changed = 0
    latest_file: Optional[Path] = None
    latest_mtime = ""

    for src in transcript_files:
        chat_id = src.parent.name
        digest = _sha256_file(src)
        prev = chats_state.get(chat_id) or {}
        if prev.get("sha256") == digest and (ARCHIVE_DIR / f"{chat_id}.md").is_file():
            index_entries.append(
                {
                    "id": chat_id,
                    "title": str(prev.get("title") or chat_id),
                    "file": f"{chat_id}.md",
                    "date": str(prev.get("date") or ""),
                    "mtime": str(prev.get("mtime") or ""),
                }
            )
            if str(prev.get("mtime") or "") > latest_mtime:
                latest_mtime = str(prev.get("mtime"))
                latest_file = ARCHIVE_DIR / f"{chat_id}.md"
            continue

        messages = _parse_jsonl(src)
        if not messages:
            continue

        title = _title_from_messages([(r, t) for r, t, _ in messages])
        project_slug = src.parts[-4] if len(src.parts) >= 4 else "unknown"
        md_name = f"{chat_id}.md"
        md_path = ARCHIVE_DIR / md_name
        md_body = _format_markdown(
            chat_id=chat_id,
            source=src,
            project_slug=project_slug,
            messages=messages,
            exported_at=exported_at,
            host=host,
        )

        mtime = datetime.fromtimestamp(src.stat().st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        date = mtime[:10]

        if dry_run:
            print(f"[dry-run] export {src} -> {md_path}")
        else:
            md_path.write_text(md_body, encoding="utf-8")

        chats_state[chat_id] = {
            "sha256": digest,
            "title": title,
            "date": date,
            "mtime": mtime,
            "source": str(src),
            "host": host,
        }
        index_entries.append(
            {
                "id": chat_id,
                "title": title,
                "file": md_name,
                "date": date,
                "mtime": mtime,
            }
        )
        changed += 1
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_file = md_path

    if dry_run:
        return {"changed": changed, "total_sources": len(transcript_files)}

    state["chats"] = chats_state
    state["last_sync_utc"] = exported_at
    state["host"] = host
    _save_state(state)
    _write_index(index_entries)
    _write_handoff(index_entries, latest_file)

    return {
        "changed": changed,
        "total_sources": len(transcript_files),
        "archive_dir": str(ARCHIVE_DIR),
    }


def git_push_chats(repo_root: Path, *, dry_run: bool = False) -> bool:
    paths = [
        ".cursor/chats/",
        "scripts/sync_cursor_chats.py",
        "scripts/sync_cursor_chats.ps1",
        "scripts/register_cursor_chats_sync_task.ps1",
    ]
    for rel in paths:
        abs_path = repo_root / rel
        if abs_path.exists():
            if dry_run:
                print(f"[dry-run] git add {rel}")
            else:
                proc = _run_git(["add", "--", rel], cwd=repo_root)
                if proc.returncode != 0:
                    print(proc.stderr or proc.stdout, file=sys.stderr)
                    return False

    status = _run_git(["status", "--porcelain", "--", *paths], cwd=repo_root)
    if dry_run:
        print(status.stdout or "[dry-run] no git status")
        return True

    if not (status.stdout or "").strip():
        print("Git: нет изменений для push (чаты уже актуальны).")
        return True

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = f"chore(cursor): sync chats {stamp}"
    commit = _run_git(["commit", "-m", msg], cwd=repo_root)
    if commit.returncode != 0:
        print(commit.stderr or commit.stdout, file=sys.stderr)
        return False

    push = _run_git(["push", "origin", "HEAD"], cwd=repo_root)
    if push.returncode != 0:
        print(push.stderr or push.stdout, file=sys.stderr)
        return False

    print(push.stdout or "Push OK")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Cursor agent transcripts into repo.")
    parser.add_argument("--repo", type=Path, default=ROOT, help="Repo root")
    parser.add_argument("--push", action="store_true", help="git commit + push chat files")
    parser.add_argument("--dry-run", action="store_true", help="only print actions")
    args = parser.parse_args(argv)

    repo_root = args.repo.resolve()
    result = sync_chats(repo_root, dry_run=args.dry_run)
    print(
        f"Cursor chats: sources={result.get('total_sources', 0)}, "
        f"updated={result.get('changed', 0)}, dir={result.get('archive_dir', CHATS_DIR)}"
    )

    if args.push:
        ok = git_push_chats(repo_root, dry_run=args.dry_run)
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
