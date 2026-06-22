"""GitHub-синхронизация Hermes → репозиторий Analise_Hermes."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from prd_agent.learning.hermes_cursor_feed import (
    CURSOR_LIVE_FILENAME,
    FEED_JSONL_NAME,
    append_jsonl,
    build_cursor_brief,
    build_feed_event,
    report_fingerprint,
)
from prd_agent.learning.winning_entry_rules import (
    WinningEntryRulesReport,
    build_markdown_report,
)

GITHUB_REPO_DEFAULT = "https://github.com/gogi-eng/Analise_Hermes.git"
META_JSON_NAME = "meta.json"
RULES_JSON_NAME = "winning_entry_rules.json"
RULES_MD_NAME = "winning_entry_rules_report.md"


def write_github_sync_files(
    report: WinningEntryRulesReport,
    github_dir: Path,
    *,
    source_label: str = "PRD-BOT",
    host_hint: str = "",
    append_history: bool = True,
) -> Tuple[Path, Path, Path]:
    """Записать артефакты в клон Analise_Hermes (корень репозитория)."""
    github_dir = Path(github_dir)
    github_dir.mkdir(parents=True, exist_ok=True)

    brief = build_cursor_brief(
        report, source_label=source_label, host_hint=host_hint
    )
    fp = report_fingerprint(report)

    live_path = github_dir / CURSOR_LIVE_FILENAME
    live_path.write_text(brief, encoding="utf-8")

    rules_json = github_dir / RULES_JSON_NAME
    rules_json.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rules_md = github_dir / RULES_MD_NAME
    rules_md.write_text(build_markdown_report(report), encoding="utf-8")

    feed_path = github_dir / FEED_JSONL_NAME
    if append_history:
        append_jsonl(
            feed_path,
            build_feed_event(
                report,
                source_label=source_label,
                cursor_path=CURSOR_LIVE_FILENAME,
                fingerprint=fp,
            ),
        )

    meta_path = github_dir / META_JSON_NAME
    meta_path.write_text(
        json.dumps(
            {
                "updated_at": report.generated_at,
                "source": source_label,
                "host": host_hint,
                "fingerprint": fp,
                "lookback_hours": report.hours,
                "repo": GITHUB_REPO_DEFAULT,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return live_path, feed_path, meta_path


def _run_git(args: List[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def git_commit_and_push(
    github_dir: Path,
    message: str,
    *,
    remote: str = "origin",
    branch: str = "",
) -> bool:
    """git add -A, commit, push. Возвращает True если был push."""
    github_dir = Path(github_dir)
    if not (github_dir / ".git").is_dir():
        raise FileNotFoundError(
            f"Не git-репозиторий: {github_dir}. "
            f"Клонируйте: git clone {GITHUB_REPO_DEFAULT}"
        )

    add = _run_git(["add", "-A"], github_dir)
    if add.returncode != 0:
        raise RuntimeError(add.stderr or add.stdout or "git add failed")

    diff = _run_git(["diff", "--cached", "--quiet"], github_dir)
    if diff.returncode == 0:
        print("GitHub: нет изменений для коммита")
        return False

    commit = _run_git(["commit", "-m", message], github_dir)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr or commit.stdout or "git commit failed")

    push_args = ["push", remote]
    if branch:
        push_args.append(branch)
    push = _run_git(push_args, github_dir)
    if push.returncode != 0:
        raise RuntimeError(push.stderr or push.stdout or "git push failed")

    print(f"GitHub: push OK → {GITHUB_REPO_DEFAULT}")
    return True


def ensure_github_clone(github_dir: Path, repo_url: str = GITHUB_REPO_DEFAULT) -> Path:
    """Клонировать Analise_Hermes если папки ещё нет."""
    github_dir = Path(github_dir)
    if (github_dir / ".git").is_dir():
        pull = _run_git(["pull", "--rebase", "origin", "main"], github_dir)
        if pull.returncode != 0:
            pull = _run_git(["pull", "--rebase", "origin", "master"], github_dir)
        if pull.returncode != 0:
            print(f"git pull warning: {pull.stderr or pull.stdout}")
        return github_dir

    github_dir.parent.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        ["git", "clone", repo_url, str(github_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if clone.returncode != 0:
        raise RuntimeError(clone.stderr or clone.stdout or "git clone failed")
    return github_dir
