# pytest conftest for backend/tests
# Makes legacy/bot importable as bot.* and skips tests referencing removed files.
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY = str(ROOT / "legacy")
if LEGACY not in sys.path:
    sys.path.insert(0, LEGACY)

# Tests that import modules/files no longer present in the repo.
collect_ignore = [
    "test_iteration44_signal_quality_fixes.py",
    "test_iteration68_exchange_closed_reason_detail.py",
    "test_iteration70_allocator_renormalization_for_slots.py",
    "test_iteration71_exchange_close_meta_and_manual_trailing_profile.py",
    "test_iteration72_position_size_mode_margin_cap.py",
    "test_iteration73_runtime_sizing_param_and_manual_min_distance.py",
    "test_local_advisor.py",
]
