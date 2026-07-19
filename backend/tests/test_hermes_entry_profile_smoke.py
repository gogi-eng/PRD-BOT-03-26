"""Smoke: Hermes отключён; торговые пороги не зависят от Hermes advisor."""
from __future__ import annotations

import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(name: str) -> dict:
    path = ROOT / "deploy" / name
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def test_sandbox_hermes_disabled():
    cfg = _load_yaml("config.agent_world_sandbox.yaml")
    assert cfg.get("hermes", {}).get("enabled") is False
    link = cfg["supervisor_v4"].get("hermes_link") or {}
    assert link.get("respect_entry_profile") is False
    assert (link.get("hermes_bypass") or {}).get("enabled") is False


def test_production_hermes_disabled():
    cfg = _load_yaml("config.production.yaml")
    assert cfg.get("hermes", {}).get("enabled") is False
    link = cfg["supervisor_v4"].get("hermes_link") or {}
    assert link.get("respect_entry_profile") is False
    assert (link.get("hermes_bypass") or {}).get("enabled") is False
    assert cfg["quality_gate"]["min_confidence"] >= 0.85
