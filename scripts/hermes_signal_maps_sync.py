#!/usr/bin/env python3
"""
Экспорт карт сигналов → GitHub Analise_Hermes (каждые 3 ч).

  ./venv/bin/python3 scripts/hermes_signal_maps_sync.py --git-clone --git-push

Сервер:
  HERMES_GITHUB_DIR=/root/Analise_Hermes
  HERMES_SOURCE=PRD-BOT-ALL
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config  # noqa: E402
from prd_agent.learning.hermes_github_sync import (  # noqa: E402
    GITHUB_REPO_DEFAULT,
    ensure_github_clone,
    git_commit_and_push,
    write_signal_maps_to_github,
)
from prd_agent.learning.hermes_signal_maps import (  # noqa: E402
    HermesSignalMapBuilder,
    write_signal_maps_artifacts,
)


def _default_github_dir() -> Path:
    env = (os.environ.get("HERMES_GITHUB_DIR") or "").strip()
    if env:
        return Path(env)
    sibling = ROOT.parent / "Analise_Hermes"
    if sibling.is_dir():
        return sibling
    return Path.home() / "Analise_Hermes"


def _source_label() -> str:
    explicit = (os.environ.get("HERMES_SOURCE") or "").strip()
    if explicit:
        return explicit
    cwd = str(Path.cwd())
    if "AGENT-WORLD" in cwd:
        return "AGENT-WORLD"
    if "PRD-BOT-ALL" in cwd:
        return "PRD-BOT-ALL"
    return "PRD-BOT"


def _host_hint() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""


def _trailing_from_config(cfg_path: Path) -> tuple[float, float]:
    activation = 1.8
    distance = 0.8
    if cfg_path.is_file():
        cfg = load_config(cfg_path)
        pos = cfg.get("positions", {}) if isinstance(cfg.get("positions"), dict) else {}
        activation = float(pos.get("sr_trail_at_profit_pct", pos.get("trailing_activation_pct", activation)) or activation)
        distance = float(pos.get("trailing_distance_pct", distance) or distance)
    return activation, distance


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes signal maps → Analise_Hermes")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--source", default="")
    ap.add_argument("--github-dir", default=str(_default_github_dir()))
    ap.add_argument("--github-repo", default=GITHUB_REPO_DEFAULT)
    ap.add_argument("--git-clone", action="store_true")
    ap.add_argument("--git-push", action="store_true")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    source_label = args.source or _source_label()
    activation, distance = _trailing_from_config(Path(args.config))

    builder = HermesSignalMapBuilder(
        data_dir,
        trailing_activation_pct=activation,
        trailing_distance_pct=distance,
    )
    maps = builder.build_maps(hours=float(args.hours))
    print(f"Карт сигналов: {len(maps)} (окно {args.hours} ч)")

    learning_dir = data_dir / "learning"
    jsonl_local, md_local = write_signal_maps_artifacts(
        maps,
        learning_dir,
        source_label=source_label,
        hours=float(args.hours),
    )
    print(f"Локально: {jsonl_local}")
    print(f"Локально: {md_local}")

    gdir = Path(args.github_dir)
    if args.git_clone:
        ensure_github_clone(gdir, args.github_repo)

    dest_jsonl, dest_md = write_signal_maps_to_github(
        jsonl_local,
        md_local,
        gdir,
        source_label=source_label,
        hours=float(args.hours),
        signal_count=len(maps),
    )
    print(f"GitHub: {dest_jsonl}")
    print(f"GitHub: {dest_md}")

    if args.git_push:
        host = _host_hint()
        msg = f"hermes: signal_maps {source_label} n={len(maps)} host={host}"
        git_commit_and_push(gdir, msg)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
