"""GitHub-синхронизация Hermes → репозиторий Analise_Hermes."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from prd_agent.learning.hermes_cursor_feed import (
    CURSOR_LIVE_FILENAME,
    FEED_JSONL_NAME,
    append_jsonl,
    build_cursor_brief,
    build_feed_event,
    report_fingerprint,
)
from prd_agent.learning.hermes_signal_maps import (
    SIGNAL_MAPS_JSONL,
    SIGNAL_MAPS_MD,
)
from prd_agent.learning.winning_entry_rules import (
    WinningEntryRulesReport,
    build_markdown_report,
)

GITHUB_REPO_HTTPS = "https://github.com/gogi-eng/Analise_Hermes.git"
GITHUB_REPO_SSH = "git@github.com:gogi-eng/Analise_Hermes.git"
GITHUB_REPO_DEFAULT = GITHUB_REPO_SSH
DEFAULT_DEPLOY_KEY = Path("/root/.ssh/analise_hermes_deploy")
GIT_COMMIT_NAME = "PRD-BOT Hermes"
GIT_COMMIT_EMAIL = "hermes-bot@users.noreply.github.com"
META_JSON_NAME = "meta.json"
RULES_JSON_NAME = "winning_entry_rules.json"
RULES_MD_NAME = "winning_entry_rules_report.md"


def write_signal_maps_to_github(
    maps_jsonl: Path,
    maps_md: Path,
    github_dir: Path,
    *,
    source_label: str,
    hours: float,
    signal_count: int,
) -> Tuple[Path, Path]:
    """Копирует hermes_signal_maps.* в клон Analise_Hermes."""
    github_dir = Path(github_dir)
    github_dir.mkdir(parents=True, exist_ok=True)
    dest_jsonl = github_dir / SIGNAL_MAPS_JSONL
    dest_md = github_dir / SIGNAL_MAPS_MD
    dest_jsonl.write_text(Path(maps_jsonl).read_text(encoding="utf-8"), encoding="utf-8")
    dest_md.write_text(Path(maps_md).read_text(encoding="utf-8"), encoding="utf-8")

    meta_path = github_dir / META_JSON_NAME
    meta: Dict[str, object] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    meta["signal_maps_updated_at"] = datetime.now(timezone.utc).isoformat()
    meta["signal_maps_count"] = signal_count
    meta["signal_maps_jsonl"] = SIGNAL_MAPS_JSONL
    meta["signal_maps_md"] = SIGNAL_MAPS_MD
    meta["signal_maps_source"] = source_label
    meta["signal_maps_hours"] = hours
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest_jsonl, dest_md


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
    meta: Dict[str, object] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    meta.update(
        {
            "updated_at": report.generated_at,
            "source": source_label,
            "host": host_hint,
            "fingerprint": fp,
            "lookback_hours": report.hours,
            "repo": GITHUB_REPO_DEFAULT,
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return live_path, feed_path, meta_path


def _git_commit_env() -> Dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": GIT_COMMIT_NAME,
        "GIT_AUTHOR_EMAIL": GIT_COMMIT_EMAIL,
        "GIT_COMMITTER_NAME": GIT_COMMIT_NAME,
        "GIT_COMMITTER_EMAIL": GIT_COMMIT_EMAIL,
    }


def _run_git(
    args: List[str],
    cwd: Path,
    *,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def deploy_key_path() -> Path:
    custom = (os.environ.get("HERMES_GITHUB_SSH_KEY") or "").strip()
    return Path(custom) if custom else DEFAULT_DEPLOY_KEY


def configure_github_ssh_push(github_dir: Path) -> None:
    """SSH remote + deploy key (без HTTPS-пароля)."""
    github_dir = Path(github_dir)
    key = deploy_key_path()
    if key.is_file():
        ssh_cmd = (
            f"ssh -i {key} -o IdentitiesOnly=yes "
            "-o StrictHostKeyChecking=accept-new"
        )
        _run_git(["config", "core.sshCommand", ssh_cmd], github_dir)
    _run_git(["remote", "set-url", "origin", GITHUB_REPO_SSH], github_dir)


def ensure_git_identity(github_dir: Path) -> None:
    """Локальный user.name/email только в Analise_Hermes (без --global)."""
    github_dir = Path(github_dir)
    name = _run_git(["config", "user.name"], github_dir)
    if not (name.stdout or "").strip():
        _run_git(["config", "user.name", GIT_COMMIT_NAME], github_dir)
    email = _run_git(["config", "user.email"], github_dir)
    if not (email.stdout or "").strip():
        _run_git(["config", "user.email", GIT_COMMIT_EMAIL], github_dir)


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

    ensure_git_identity(github_dir)
    configure_github_ssh_push(github_dir)

    add = _run_git(["add", "-A"], github_dir)
    if add.returncode != 0:
        raise RuntimeError(add.stderr or add.stdout or "git add failed")

    diff = _run_git(["diff", "--cached", "--quiet"], github_dir)
    if diff.returncode == 0:
        print("GitHub: нет изменений для коммита")
        return False

    commit = _run_git(
        ["commit", "-m", message],
        github_dir,
        extra_env=_git_commit_env(),
    )
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr or commit.stdout or "git commit failed")

    push_args = ["push", remote]
    if branch:
        push_args.append(branch)
    push = _run_git(push_args, github_dir)
    if push.returncode != 0:
        key = deploy_key_path()
        hint = (
            f"git push failed. Настройте Deploy Key:\n"
            f"  ssh-keygen -t ed25519 -f {key} -N \"\"\n"
            f"  cat {key}.pub  → GitHub Analise_Hermes → Deploy keys (Allow write)\n"
            f"  bash deploy/hermes/install_analise_hermes_github.sh"
        )
        raise RuntimeError(f"{push.stderr or push.stdout or 'git push failed'}\n{hint}")

    print(f"GitHub: push OK → {GITHUB_REPO_DEFAULT}")
    return True


def ensure_github_clone(github_dir: Path, repo_url: str = GITHUB_REPO_DEFAULT) -> Path:
    """Клонировать Analise_Hermes если папки ещё нет."""
    github_dir = Path(github_dir)
    if (github_dir / ".git").is_dir():
        configure_github_ssh_push(github_dir)
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
    ensure_git_identity(github_dir)
    configure_github_ssh_push(github_dir)
    return github_dir
