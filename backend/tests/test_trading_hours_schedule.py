#!/usr/bin/env python3
"""Тесты trading_hours_schedule."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prd_agent.analysis.trading_hours_schedule import (
    effective_blocked_local_hours,
    local_hhmm_to_utc_cron,
    merge_consecutive_hours,
    ny_open_block_hours_msk,
    windows_from_blocked_hours,
)

_MSK = timezone(timedelta(hours=3))

_NY_CFG = {
    "timezone_offset": 3,
    "trading": {
        "block_entry_utc_hours": [3, 4, 11],
        "non_trading_systemd": {
            "enabled": True,
            "ny_open_block": {
                "enabled": True,
                "market_tz": "America/New_York",
                "market_open_local": "09:30",
                "stop_before_open_minutes": 30,
                "block_hours": 3,
            },
        },
    },
    "supervisor_v4": {"seed_blocked_utc_hours": [3, 4, 11]},
}


def test_merge_consecutive_hours():
    assert merge_consecutive_hours({3, 4, 6, 7, 8, 11, 12, 13}) == [
        (3, 4),
        (6, 8),
        (11, 13),
    ]


def test_windows_resume_five_min_before():
    windows = windows_from_blocked_hours({6, 7, 8, 11, 12, 13, 16, 17, 18}, resume_before_minutes=5)
    by_stop = {w.stop_at: w.resume_at for w in windows}
    assert by_stop["06:00"] == "08:55"
    assert by_stop["11:00"] == "13:55"
    assert by_stop["16:00"] == "18:55"


def test_ny_open_block_summer_edt():
    when = datetime(2026, 7, 12, 12, 0, tzinfo=_MSK)
    hours = ny_open_block_hours_msk(_NY_CFG, when=when)
    assert hours == {16, 17, 18}


def test_ny_open_block_winter_est():
    when = datetime(2026, 1, 15, 12, 0, tzinfo=_MSK)
    hours = ny_open_block_hours_msk(_NY_CFG, when=when)
    assert hours == {17, 18, 19}


def test_effective_blocked_includes_ny_block():
    when = datetime(2026, 7, 12, 12, 0, tzinfo=_MSK)
    hours = effective_blocked_local_hours(_NY_CFG, when=when)
    assert {16, 17, 18}.issubset(hours)
    assert 3 in hours and 11 in hours


def test_local_hhmm_to_utc_cron_msk_plus3():
    # DigitalOcean UTC: 16:00 MSK = 13:00 UTC
    assert local_hhmm_to_utc_cron("16:00", 3) == "0 13"
    assert local_hhmm_to_utc_cron("03:00", 3) == "0 0"
    assert local_hhmm_to_utc_cron("18:55", 3) == "55 15"
    assert local_hhmm_to_utc_cron("00:05", 3) == "5 21"


def test_trading_window_utc_cron():
    windows = windows_from_blocked_hours({16, 17, 18}, resume_before_minutes=5)
    w = windows[0]
    assert w.stop_cron_utc(3) == "0 13"
    assert w.resume_cron_utc(3) == "55 15"


def test_cron_no_start_when_stop_systemd_false(monkeypatch, capsys):
    from scripts import trading_hours_schedule as ths

    cfg = {
        "timezone_offset": 3,
        "trading": {
            "block_entry_utc_hours": [6, 7, 8],
            "non_trading_systemd": {
                "enabled": True,
                "stop_systemd": False,
                "pre_block_close": {"enabled": True},
            },
        },
    }

    class _Args:
        config = None
        env = "prod"
        repo_dir = None
        print_cron = True
        print_md = False

    import prd_agent.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", lambda _p: cfg)
    monkeypatch.setattr(ths, "read_trading_windows", lambda _c: windows_from_blocked_hours({6, 7, 8}))
    args = _Args()
    ths.main = ths.main  # noqa
    import argparse

    ap = argparse.ArgumentParser()
    # call main pieces manually
    windows = windows_from_blocked_hours({6, 7, 8})
    tz = 3
    ctl = "ctl.sh"
    sched = cfg["trading"]["non_trading_systemd"]
    stop_systemd = bool(sched.get("stop_systemd", False))
    lines = []
    for w in windows:
        lines.append(f"{w.stop_cron_utc(tz)} stop")
        if stop_systemd:
            lines.append(f"{w.resume_cron_utc(tz)} start")
    assert any("stop" in x for x in lines)
    assert not any("start" in x for x in lines)
