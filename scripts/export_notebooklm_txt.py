"""Export PRD-BOT-ALL files as plain .txt for NotebookLM upload."""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

TEXT_COPIES = [
    ("config.yaml", "config.txt"),
    ("config.example.yaml", "config.example.txt"),
    ("telegram_signal_agent_state.json", "telegram_signal_agent_state.txt"),
    ("telegram_signal_agent.log", "telegram_signal_agent.txt"),
    ("bot.log", "bot.txt"),
]


def export_text_copy(src_name: str, dst_name: str) -> None:
    src = BASE / src_name
    dst = BASE / dst_name
    if not src.exists():
        print(f"SKIP missing: {src_name}")
        return
    data = src.read_text(encoding="utf-8", errors="replace")
    dst.write_text(data, encoding="utf-8")
    print(f"OK {src_name} -> {dst_name} ({len(data):,} chars)")


def export_session(session_name: str = "telegram_user_signal_agent.session") -> None:
    session = BASE / session_name
    out = BASE / f"{session_name}.txt"
    if not session.exists():
        print(f"SKIP missing: {session_name}")
        return

    lines = [
        "# Telegram session file (SQLite) — text export for NotebookLM",
        "# Binary auth blobs omitted. Prefer not uploading this to cloud.",
        f"# Source: {session_name}",
        "",
    ]
    conn = sqlite3.connect(str(session))
    cur = conn.cursor()
    tables = [
        r[0]
        for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    for table in tables:
        lines.append(f"## Table: {table}")
        cols = cur.execute(f"PRAGMA table_info({table})").fetchall()
        col_names = [c[1] for c in cols]
        lines.append("Columns: " + ", ".join(col_names))
        rows = cur.execute(f"SELECT * FROM [{table}]").fetchall()
        lines.append(f"Row count: {len(rows)}")
        for i, row in enumerate(rows[:50]):
            parts = []
            for name, val in zip(col_names, row):
                if isinstance(val, (bytes, memoryview)):
                    b = bytes(val)
                    parts.append(f"{name}=[binary {len(b)} bytes, omitted]")
                elif val is None:
                    parts.append(f"{name}=NULL")
                else:
                    s = str(val)
                    if len(s) > 200:
                        s = s[:200] + "..."
                    parts.append(f"{name}={s!r}")
            lines.append(f"  row {i}: " + "; ".join(parts))
        if len(rows) > 50:
            lines.append(f"  ... {len(rows) - 50} more rows omitted")
        lines.append("")
    conn.close()
    text = "\n".join(lines)
    out.write_text(text, encoding="utf-8")
    print(f"OK {session_name} -> {out.name} ({len(text):,} chars)")


def main() -> None:
    for src, dst in TEXT_COPIES:
        export_text_copy(src, dst)
    export_session()


if __name__ == "__main__":
    main()
