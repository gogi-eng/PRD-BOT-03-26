#!/usr/bin/env python3
"""
Hermes → Cursor через GitHub (Analise_Hermes).

Сервер AGENT-WORLD:
  export HERMES_GITHUB_DIR=/root/Analise_Hermes
  ./venv/bin/python3 scripts/hermes_cursor_feed.py --watch --git-push --hours 336

Любой ПК (после git pull в Analise_Hermes):
  powershell -File scripts/hermes_sync_from_github.ps1
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.learning.hermes_cursor_feed import (  # noqa: E402
    FEED_JSONL_NAME,
    build_cursor_brief,
    data_sources_mtime,
    load_last_feed_fingerprint,
    report_fingerprint,
    write_cursor_feed_files,
)
from prd_agent.learning.hermes_github_sync import (  # noqa: E402
    GITHUB_REPO_DEFAULT,
    ensure_github_clone,
    git_commit_and_push,
    write_github_sync_files,
)
from prd_agent.learning.winning_entry_rules import (  # noqa: E402
    WinningEntryRulesAnalyzer,
    WinningEntryRulesReport,
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


def _load_report_from_json(path: Path) -> WinningEntryRulesReport:
    import json
    from prd_agent.learning.winning_entry_rules import (
        FilterImpactStat,
        RuleSuggestion,
        SkipFilterReview,
        WeightRecommendation,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    return WinningEntryRulesReport(
        hours=float(raw.get("hours", 168)),
        tp_winners=int(raw.get("tp_winners", 0)),
        tp_skipped_virtual=int(raw.get("tp_skipped_virtual", 0)),
        tp_opened_real=int(raw.get("tp_opened_real", 0)),
        sl_losers=int(raw.get("sl_losers", 0)),
        outcome_counts=dict(raw.get("outcome_counts") or {}),
        rules=[RuleSuggestion(**r) for r in raw.get("rules") or []],
        winner_feature_medians=dict(raw.get("winner_feature_medians") or {}),
        top_skip_reasons_on_tp=dict(raw.get("top_skip_reasons_on_tp") or {}),
        filter_impacts=[FilterImpactStat(**f) for f in raw.get("filter_impacts") or []],
        weight_recommendations=[
            WeightRecommendation(**w) for w in raw.get("weight_recommendations") or []
        ],
        skip_filter_reviews=[
            SkipFilterReview(**s) for s in raw.get("skip_filter_reviews") or []
        ],
        suggested_rule_weights=dict(raw.get("suggested_rule_weights") or {}),
        generated_at=str(raw.get("generated_at") or ""),
    )


def _analyze(data_dir: Path, hours: float) -> WinningEntryRulesReport:
    return WinningEntryRulesAnalyzer(data_dir).analyze(hours=hours)


async def _maybe_telegram(report: WinningEntryRulesReport, brief_head: str) -> bool:
    from prd_agent.config import load_config
    from prd_agent.telegram.notifier import TelegramNotifier

    cfg_path = ROOT / "config.yaml"
    if not cfg_path.is_file():
        print("Telegram: config.yaml не найден, пропуск", file=sys.stderr)
        return False
    cfg = load_config(cfg_path)
    notifier = TelegramNotifier(cfg)
    zero = brief_head.split("## ⚡")[1].split("##")[0].strip() if "## ⚡" in brief_head else ""
    text = (
        "<b>🧠 Hermes → GitHub</b>\n"
        f"Профит: <b>{report.outcome_counts.get('profit', 0)}</b> | "
        f"Убыток: <b>{report.outcome_counts.get('loss', 0)}</b>\n"
    )
    if zero:
        text += f"\n<b>ZeroOne:</b>\n<pre>{zero[:900]}</pre>\n"
    text += f"\n<i>{GITHUB_REPO_DEFAULT}</i>"
    return await notifier.send(text)


def _fingerprint_path(args: argparse.Namespace) -> Path:
    if args.github_dir:
        return Path(args.github_dir) / FEED_JSONL_NAME
    return Path(args.data_dir) / "learning" / FEED_JSONL_NAME


def _publish(
    report: WinningEntryRulesReport,
    args: argparse.Namespace,
    *,
    source_label: str,
    host_hint: str,
) -> bool:
    fp = report_fingerprint(report)
    feed_path = _fingerprint_path(args)
    if not args.force and load_last_feed_fingerprint(feed_path) == fp:
        print(f"Без изменений (fingerprint={fp}), пропуск")
        return False

    if not args.github_only:
        cursor_p, mirror_p, feed_p = write_cursor_feed_files(
            report,
            repo_root=Path(args.repo_root),
            data_dir=Path(args.data_dir),
            source_label=source_label,
            host_hint=host_hint,
        )
        print(f"Cursor: {cursor_p}")
        print(f"Зеркало: {mirror_p}")
        print(f"История: {feed_p}")

    if args.github_dir:
        gdir = Path(args.github_dir)
        if args.git_clone:
            ensure_github_clone(gdir, args.github_repo)
        live_p, feed_p, meta_p = write_github_sync_files(
            report,
            gdir,
            source_label=source_label,
            host_hint=host_hint,
        )
        print(f"GitHub live: {live_p}")
        print(f"GitHub feed: {feed_p}")
        print(f"GitHub meta: {meta_p}")

        if args.git_push:
            msg = (
                f"hermes: {source_label} fp={fp} "
                f"profit={report.outcome_counts.get('profit', 0)} "
                f"loss={report.outcome_counts.get('loss', 0)}"
            )
            git_commit_and_push(gdir, msg, branch=args.git_branch)

    return True


def run_once(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    source_label = args.source or _source_label()
    host_hint = args.host or _host_hint()

    if args.from_json:
        report = _load_report_from_json(Path(args.from_json))
    else:
        report = _analyze(data_dir, float(args.hours))
        if not args.no_save:
            WinningEntryRulesAnalyzer(data_dir).save(report)

    changed = _publish(
        report,
        args,
        source_label=source_label,
        host_hint=host_hint,
    )

    if args.print_brief:
        print(build_cursor_brief(report, source_label=source_label, host_hint=host_hint))

    if args.telegram and changed:
        brief = build_cursor_brief(report, source_label=source_label, host_hint=host_hint)
        ok = asyncio.run(_maybe_telegram(report, brief))
        print("Telegram:", "OK" if ok else "fail")

    return 0


def run_watch(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    last_mtime = 0.0
    target = args.github_dir or (Path(args.repo_root) / ".cursor" / "HERMES_LIVE.md")
    print(f"Watch: data={data_dir} interval={args.interval}s → {target}")
    while True:
        try:
            mtime = data_sources_mtime(data_dir)
            if mtime > last_mtime:
                last_mtime = mtime
                code = run_once(args)
                if code != 0:
                    return code
            else:
                print(f"Нет новых данных (mtime={mtime:.0f})")
        except KeyboardInterrupt:
            print("\nОстановлено")
            return 0
        except Exception as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
        time.sleep(max(5.0, float(args.interval)))


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes → Cursor (GitHub Analise_Hermes)")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--repo-root", default=str(ROOT))
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--source", default="")
    ap.add_argument("--host", default="")
    ap.add_argument("--from-json", default="")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--print-brief", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=120.0)
    ap.add_argument(
        "--github-dir",
        default=str(_default_github_dir()) if os.environ.get("HERMES_GITHUB_DIR") else "",
        help="Путь к клону Analise_Hermes (или HERMES_GITHUB_DIR)",
    )
    ap.add_argument("--github-repo", default=GITHUB_REPO_DEFAULT)
    ap.add_argument("--github-only", action="store_true", help="Только GitHub, без .cursor локально")
    ap.add_argument("--git-clone", action="store_true", help="Клонировать/pull перед записью")
    ap.add_argument("--git-push", action="store_true", help="git commit + push после записи")
    ap.add_argument("--git-branch", default="", help="Ветка для push (по умолчанию текущая)")
    args = ap.parse_args()

    if not args.github_dir and (args.git_push or args.git_clone or args.github_only):
        args.github_dir = str(_default_github_dir())

    if args.watch:
        return run_watch(args)
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
