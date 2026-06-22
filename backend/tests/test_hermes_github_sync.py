"""Тесты Hermes → GitHub Analise_Hermes."""
from __future__ import annotations

from pathlib import Path

from prd_agent.learning.hermes_github_sync import (
    GIT_COMMIT_EMAIL,
    GIT_COMMIT_NAME,
    META_JSON_NAME,
    ensure_git_identity,
    write_github_sync_files,
)
from prd_agent.learning.winning_entry_rules import WinningEntryRulesReport


def test_write_github_sync_files(tmp_path: Path):
    report = WinningEntryRulesReport(
        hours=48,
        tp_winners=2,
        tp_skipped_virtual=1,
        tp_opened_real=1,
        sl_losers=1,
        outcome_counts={"profit": 2, "loss": 1, "neutral": 0},
    )
    live, feed, meta = write_github_sync_files(
        report, tmp_path, source_label="AGENT-WORLD", host_hint="testhost"
    )
    assert live.name == "HERMES_LIVE.md"
    assert feed.name == "hermes_cursor_feed.jsonl"
    assert meta.name == META_JSON_NAME
    assert "hermes_feed: true" in live.read_text(encoding="utf-8")
    assert feed.read_text(encoding="utf-8").strip()


def test_ensure_git_identity_local_only(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess_mod = __import__("subprocess")
    subprocess_mod.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    ensure_git_identity(repo)
    name = subprocess_mod.run(
        ["git", "config", "user.name"], cwd=repo, check=True, capture_output=True, text=True
    )
    email = subprocess_mod.run(
        ["git", "config", "user.email"], cwd=repo, check=True, capture_output=True, text=True
    )
    assert name.stdout.strip() == GIT_COMMIT_NAME
    assert email.stdout.strip() == GIT_COMMIT_EMAIL
