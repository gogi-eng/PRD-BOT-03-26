#!/usr/bin/env python3
"""
Train transformer outside the live bot (use with cron when feedback_loop.retrain_in_process=false).

  cd /path/to/repo && ./venv/bin/python scripts/feedback_retrain_once.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import BotConfig
from train_transformer import train as train_transformer_model


def _parse_iso_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _is_quality_feedback_row(cfg: BotConfig, row: dict) -> bool:
    if row.get("source") != "signal_only_feedback":
        return False
    if row.get("exit_reason") not in {"stop_loss", "take_profit"}:
        return False
    min_p = float(cfg.get("feedback_loop", "min_feedback_label_abs_pnl_pct", default=0.4) or 0.4)
    if abs(float(row.get("pnl_pct", 0) or 0)) < min_p:
        return False
    entry_dt = _parse_iso_dt(row.get("entry_time"))
    exit_dt = _parse_iso_dt(row.get("exit_time"))
    if not entry_dt or not exit_dt:
        return False
    hold = (exit_dt - entry_dt).total_seconds() / 60.0
    return hold >= float(cfg.get("feedback_loop", "min_feedback_label_hold_minutes", default=8) or 8)


def _resolve_path(bd: Path, p: str | Path) -> Path:
    pp = Path(p)
    return pp if pp.is_absolute() else (bd / pp)


def main() -> int:
    bd = ROOT
    cfg = BotConfig.load(str(bd / "config.yaml"))

    use_merged = bool(cfg.get("feedback_loop", "use_merged_dataset_for_retrain", default=True))
    if not use_merged:
        ds = cfg.get("feedback_loop", "dataset_path", default="signal_only_feedback_data.json")
        data_path = _resolve_path(bd, str(ds))
    else:
        base_p = _resolve_path(bd, str(cfg.get("feedback_loop", "base_dataset_path", default="training_data.json")))
        fb_p = _resolve_path(bd, str(cfg.get("feedback_loop", "dataset_path", default="signal_only_feedback_data.json")))
        base_rows: list = []
        if base_p.exists():
            with open(base_p, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    base_rows = [r for r in loaded if r.get("result") in {"win", "loss"}]
        fb_rows: list = []
        if fb_p.exists():
            with open(fb_p, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    fb_rows = [r for r in loaded if _is_quality_feedback_row(cfg, r)]
        merged = base_rows + fb_rows
        data_path = bd / "training_data_merged.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(
            f"[feedback_retrain_once] dataset merged base={len(base_rows)} "
            f"quality_feedback={len(fb_rows)} total={len(merged)} -> {data_path.name}"
        )

    wrel = str(cfg.get("entry", "trained_model_weights_path", default="bot/transformer_weights.pt") or "bot/transformer_weights.pt")
    output_path = str(_resolve_path(bd, wrel))

    ok = train_transformer_model(
        data_path=str(data_path),
        epochs=int(cfg.get("feedback_loop", "train_epochs", default=220) or 220),
        lr=float(cfg.get("feedback_loop", "train_lr", default=0.002) or 0.002),
        batch_size=int(cfg.get("feedback_loop", "train_batch_size", default=32) or 32),
        output_path=output_path,
        val_ratio=float(cfg.get("feedback_loop", "train_val_ratio", default=0.2) or 0.2),
        decision_threshold=float(cfg.get("feedback_loop", "train_decision_threshold", default=0.55) or 0.55),
        seed=int(cfg.get("feedback_loop", "train_seed", default=42) or 42),
        augment_wins_factor=max(1, int(cfg.get("feedback_loop", "augment_wins_factor", default=2) or 2)),
        augment_noise_std=max(0.0, float(cfg.get("feedback_loop", "augment_noise_std", default=0.03) or 0.03)),
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
