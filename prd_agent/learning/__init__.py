"""Обучение на удачных входах (TP) — пропущенные и реальные сделки."""

from prd_agent.learning.hermes_cursor_feed import (
    build_cursor_brief,
    write_cursor_feed_files,
)
from prd_agent.learning.winning_entry_rules import (
    WinningEntryRulesAnalyzer,
    analyze_winning_entries,
    build_markdown_report,
    build_telegram_report,
    build_weight_recommendations,
    classify_outcome_quality,
)

__all__ = [
    "WinningEntryRulesAnalyzer",
    "analyze_winning_entries",
    "build_cursor_brief",
    "build_markdown_report",
    "build_telegram_report",
    "build_weight_recommendations",
    "classify_outcome_quality",
    "write_cursor_feed_files",
]
