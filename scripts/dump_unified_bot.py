#!/usr/bin/env python3
"""Дамп unified-бота (prd_agent) в один текстовый файл. Секреты не копируются."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

EXCLUDE_DIRS = {
    "venv",
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    ".pytest_cache",
    "journal_output",
    "Analise_Hermes",
    "crypto-agent-trading-main",
    "PRD-BOT-03-26",
    "data",
    "reports",
    "backtest_out",
}

EXCLUDE_FILE_NAMES = {
    "BOT_DUMP.txt",
    "bot.log.ALL.txt",
}

EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log"}

SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "secrets.yaml",
    "credentials.json",
    "ai4trade.credentials.json",
}

INCLUDE_EXTENSIONS = {
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".txt",
    ".md",
    ".toml",
    ".cfg",
    ".ini",
    ".sh",
    ".service",
    ".timer",
}

INCLUDE_ROOTS = (
    "prd_agent",
    "telegram_agent",
    "scripts",
    "deploy",
    "core",
    "legacy",
)

INCLUDE_FILES = (
    "run_unified.py",
    "config.example.yaml",
    "README.ru.md",
    "CONFIG_GUIDE.md",
    "TRAINING_AND_MODEL_INTEGRATION.md",
    "requirements.txt",
    "backend/requirements.txt",
)


def git_head(root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return f"{branch} @ {out[:12]}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def iter_files(root: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        rp = path.resolve()
        if rp in seen or not path.is_file():
            return
        if path.name in EXCLUDE_FILE_NAMES:
            return
        if path.suffix.lower() in EXCLUDE_SUFFIXES:
            return
        if path.name in SENSITIVE_NAMES:
            found.append(path)
            seen.add(rp)
            return
        ext = path.suffix.lower()
        if ext not in INCLUDE_EXTENSIONS and path.name not in SENSITIVE_NAMES:
            return
        try:
            rel_parts = path.resolve().relative_to(root.resolve()).parts
        except ValueError:
            return
        if set(rel_parts) & EXCLUDE_DIRS:
            return
        found.append(path)
        seen.add(rp)

    for rel in INCLUDE_ROOTS:
        base = root / rel
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for name in filenames:
                add(Path(dirpath) / name)

    for rel in INCLUDE_FILES:
        p = root / rel
        if p.is_file():
            add(p)

    return sorted(found, key=lambda p: str(p.relative_to(root)).lower())


def size_str(path: Path) -> str:
    try:
        n = path.stat().st_size
    except OSError:
        return "unknown"
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified bot project dump")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--label", required=True, help="PRD-BOT-ALL | AGENT-WORLD")
    parser.add_argument("--config", required=True, help="Active deploy config path (relative to root)")
    parser.add_argument("--output", required=True, help="Output dump file path (relative to root)")
    args = parser.parse_args()

    root = args.root.resolve()
    config_path = (root / args.config).resolve()
    output_path = (root / args.output).resolve()
    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    files = iter_files(root)
    rel_files = [p.relative_to(root) for p in files]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = git_head(root)

    with output_path.open("w", encoding="utf-8") as out:
        out.write("=" * 80 + "\n")
        out.write("PROJECT DUMP — PRD-BOT unified (run_unified.py / prd_agent)\n")
        out.write(f"Bot: {args.label}\n")
        out.write(f"Date: {now}\n")
        out.write(f"Git: {head}\n")
        out.write(f"Root: {root}\n")
        out.write(f"Active config: {args.config}\n")
        out.write("=" * 80 + "\n\n")

        out.write("FILE TREE:\n")
        out.write("-" * 40 + "\n")
        for rel in rel_files:
            mark = " [PROTECTED]" if rel.name in SENSITIVE_NAMES else ""
            out.write(f"  {rel.as_posix()}{mark}\n")
        out.write(f"\nTotal files: {len(rel_files)}\n\n")

        out.write("=" * 80 + "\n")
        out.write(f"ACTIVE CONFIG (as on server: config.yaml from {args.config})\n")
        out.write("=" * 80 + "\n\n")
        cfg_text = config_path.read_text(encoding="utf-8")
        out.write(cfg_text)
        if not cfg_text.endswith("\n"):
            out.write("\n")

        out.write("=" * 80 + "\n")
        out.write("SOURCE FILES\n")
        out.write("=" * 80 + "\n")

        for path, rel in zip(files, rel_files):
            out.write("\n" + "-" * 80 + "\n")
            out.write(f"FILE: {rel.as_posix()}\n")
            out.write(f"SIZE: {size_str(path)}\n")
            out.write("-" * 80 + "\n\n")
            if rel.name in SENSITIVE_NAMES:
                out.write("[PROTECTED — secrets not included]\n")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                out.write("[BINARY — skipped]\n")
                continue
            except OSError as exc:
                out.write(f"[READ ERROR: {exc}]\n")
                continue
            out.write(text)
            if not text.endswith("\n"):
                out.write("\n")

        out.write("\n" + "=" * 80 + "\n")
        out.write("END OF DUMP\n")
        out.write("=" * 80 + "\n")

    mb = output_path.stat().st_size / (1024 * 1024)
    print(f"OK: {output_path} ({mb:.2f} MB, {len(rel_files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
