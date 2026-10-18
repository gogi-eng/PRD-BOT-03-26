#!/usr/bin/env python3
"""Снимок живой песочницы AGENT-WORLD в один текстовый файл.

Берёт то, что уже есть на сервере: версию, службы, хвост лога, журнал сделок,
причины пропусков и config.yaml. Ключи, токены и сессия Telegram не попадают.

Запуск на сервере:
  cd /root/AGENT-WORLD
  ./venv/bin/python3 scripts/dump_agent_world_server.py
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SECRET_LINE = re.compile(
    r"(?i)^(\s*(?:[A-Z0-9_]*"
    r"(?:API_KEY|API_SECRET|BOT_TOKEN|TOKEN|SECRET|PASSWORD|HF_TOKEN)"
    r"|api_key|api_secret|bot_token|auth_token)"
    r"\s*[:=]\s*)"
    r"(?P<q>['\"]?)(?P<val>[^'\"#\n]*?)(?P=q)\s*$"
)

SERVICES = (
    "trading_bot_agent_world",
    "telegram_signal_agent_world",
)

LOG_TAILS = (
    ("bot.log", 400),
    ("telegram_signal_agent.log", 120),
)

TEXT_TAILS = (
    ("data/trades/trade_history.jsonl", 40),
    ("data/ledger/signal_ledger.jsonl", 40),
    ("gate_state.json", 80),
)


def redact_text(text: str) -> str:
    """Прячет непустые ключи и токены. Пустые поля оставляет как есть."""
    out: list[str] = []
    for line in text.splitlines():
        match = SECRET_LINE.match(line)
        if match:
            raw = (match.group("val") or "").strip()
            if raw and raw not in {"''", '""'}:
                line = f"{match.group(1)}{match.group('q')}[скрыто]{match.group('q')}"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def tail_lines(path: Path, limit: int) -> str:
    if not path.is_file():
        return f"[нет файла {path.name}]\n"
    kept: deque[str] = deque(maxlen=max(1, limit))
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            kept.append(line)
    return "".join(kept)


def run_cmd(args: list[str], *, cwd: Path) -> str:
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"[команда не выполнилась: {exc}]\n"
    text = (proc.stdout or "") + (proc.stderr or "")
    return text if text.endswith("\n") or not text else text + "\n"


def git_line(root: Path) -> str:
    rev = run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd=root).strip()
    branch = run_cmd(["git", "branch", "--show-current"], cwd=root).strip()
    return f"{branch or '(нет ветки)'} @ {rev or 'unknown'}"


def skip_baseline(root: Path, hours: float) -> str:
    script = root / "scripts" / "algo_skip_baseline.py"
    py = root / "venv" / "bin" / "python3"
    if not py.is_file():
        py = Path(sys.executable)
    if not script.is_file():
        return "[нет scripts/algo_skip_baseline.py]\n"
    return run_cmd([str(py), str(script), "--hours", str(hours), "--root", str(root)], cwd=root)


def build_dump(root: Path, *, log_lines: int, hours: float) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    parts = [
        "ПЕСОЧНИЦА AGENT-WORLD — СНИМОК С СЕРВЕРА",
        f"Дата: {now}",
        f"Корень: {root}",
        f"Git: {git_line(root)}",
        "Ключи, токены и сессия Telegram не включены.",
        "",
        "=" * 72,
        "СЛУЖБЫ",
        "=" * 72,
        "",
        run_cmd(["systemctl", "is-active", *SERVICES], cwd=root),
        run_cmd(
            ["systemctl", "status", *SERVICES, "--no-pager", "-n", "15"],
            cwd=root,
        ),
    ]
    for name, limit in LOG_TAILS:
        use = log_lines if name == "bot.log" else limit
        parts.extend(
            [
                "=" * 72,
                f"ХВОСТ {name} (последние {use} строк)",
                "=" * 72,
                "",
                redact_text(tail_lines(root / name, use)),
                "",
            ]
        )
    parts.extend(
        [
            "=" * 72,
            f"ПРОПУСКИ ЗА {hours:g} Ч",
            "=" * 72,
            "",
            redact_text(skip_baseline(root, hours)),
            "",
        ]
    )
    for rel, limit in TEXT_TAILS:
        parts.extend(
            [
                "=" * 72,
                f"ХВОСТ {rel} (последние {limit} строк)",
                "=" * 72,
                "",
                redact_text(tail_lines(root / rel, limit)),
                "",
            ]
        )
    config = root / "config.yaml"
    parts.extend(
        [
            "=" * 72,
            "config.yaml (ключи скрыты)",
            "=" * 72,
            "",
            redact_text(config.read_text(encoding="utf-8", errors="replace"))
            if config.is_file()
            else "[нет config.yaml]\n",
        ]
    )
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Снимок живой песочницы AGENT-WORLD")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--log-lines", type=int, default=400)
    parser.add_argument("--hours", type=float, default=168.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Куда писать. По умолчанию dumps/AGENT-WORLD_server_ГГГГММДД_ЧЧММСС.txt",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = args.output or (root / "dumps" / f"AGENT-WORLD_server_{stamp}.txt")
    text = build_dump(root, log_lines=args.log_lines, hours=args.hours)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"OK {out}")
    print(f"size_kb={out.stat().st_size // 1024}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
