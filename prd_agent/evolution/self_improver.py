"""
Безопасное самоулучшение: только low-risk правки config.yaml по умолчанию.
Критические изменения (.py) — в sandbox + ожидание одобрения в Telegram.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# Разрешённые ключи для авто-подстройки (числа в пределах)
LOW_RISK_TUNING = {
    ("trading", "min_signal_confidence"): (0.55, 0.85, 0.02),
    ("signals", "min_analysis_confidence"): (0.85, 0.95, 0.02),
    ("quality_gate", "min_rr_ratio"): (1.5, 2.5, 0.05),
    ("quality_gate", "min_confidence"): (0.75, 0.95, 0.02),
    ("entry_guard", "max_market_drift_pct"): (0.003, 0.012, 0.001),
    ("entry_guard", "max_limit_drift_pct"): (0.005, 0.02, 0.001),
    ("pullback_entry", "min_momentum_pct"): (0.2, 0.6, 0.05),
    ("risk", "cooldown_after_loss_sec"): (60, 900, 30),
    ("risk", "max_consecutive_losses"): (2, 6, 1),
    ("trading", "risk_pct_per_trade"): (0.1, 1.5, 0.05),
    ("positions", "breakeven_after_pct"): (0.12, 0.65, 0.04),
    ("positions", "trailing_activation_pct"): (0.25, 1.2, 0.05),
    ("positions", "trailing_distance_pct"): (0.35, 1.5, 0.05),
    ("positions", "trailing_distance_atr_mult"): (0.7, 2.5, 0.1),
}


class SelfImprover:
    def __init__(
        self,
        cfg: Dict[str, Any],
        root: Path,
        on_config_reload: Optional[callable] = None,
    ):
        self.cfg = cfg
        si = cfg.get("self_improvement", {})
        self.enabled = bool(si.get("enabled", True))
        self.auto_low = bool(si.get("auto_apply_low_risk", True))
        self.require_approval = bool(si.get("require_approval_critical", True))
        self.reload_after_apply = bool(si.get("reload_config_after_apply", True))
        self.sandbox_dir = Path(si.get("sandbox_dir", root / "data" / "sandbox"))
        self.git_auto_commit = bool(si.get("git_auto_commit", False))
        self.root = root
        self.config_path = root / "config.yaml"
        self.log_path = root / "data" / "self_improvement_log.jsonl"
        self.pending_path = root / "data" / "pending_patches.json"
        self._on_reload = on_config_reload
        self._pending_reload = False
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log_change(self, entry: Dict[str, Any]) -> None:
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def recent_changes(self, hours: float = 2) -> List[Dict]:
        if not self.log_path.exists():
            return []
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        out: List[Dict] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                if ts.timestamp() >= cutoff:
                    out.append(row)
            except (json.JSONDecodeError, KeyError, ValueError):
                pass
        return out

    def propose_from_performance(
        self, report_2h: Dict[str, Any], report_24h: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Простые правила: ухудшение → ужесточить риск; стабильный плюс → чуть ослабить фильтр."""
        proposals: List[Dict[str, Any]] = []
        pnl_2h = float(report_2h.get("pnl_usdt", 0))
        pnl_24h = float(report_24h.get("pnl_usdt", 0))
        wr_24h = float(report_24h.get("win_rate_pct", 50))

        if pnl_2h < -5 or (pnl_24h < -15 and wr_24h < 45):
            proposals.append(
                {
                    "risk": "low",
                    "path": ["trading", "min_signal_confidence"],
                    "delta": +0.02,
                    "summary": "Повысить min_signal_confidence после просадки",
                    "justification": f"PnL 2h={pnl_2h:.2f}, 24h={pnl_24h:.2f}, WR={wr_24h}%",
                }
            )
            proposals.append(
                {
                    "risk": "low",
                    "path": ["risk", "cooldown_after_loss_sec"],
                    "delta": +60,
                    "summary": "Увеличить паузу после убытка",
                    "justification": "Снизить серию убыточных входов",
                }
            )
        elif pnl_24h > 10 and wr_24h > 55:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["trading", "min_signal_confidence"],
                    "delta": -0.02,
                    "summary": "Слегка снизить порог уверенности при стабильном плюсе",
                    "justification": f"PnL 24h={pnl_24h:.2f}, WR={wr_24h}%",
                }
            )
            proposals.append(
                {
                    "risk": "low",
                    "path": ["positions", "trailing_activation_pct"],
                    "delta": -0.05,
                    "summary": "Раньше включать трейлинг при стабильном плюсе",
                    "justification": f"PnL 24h={pnl_24h:.2f}, WR={wr_24h}%",
                }
            )
        return proposals

    def propose_exit_tuning(
        self,
        *,
        real_wr_24h: float,
        real_pnl_24h: float,
        virtual_wr_24h: float,
        virtual_n_24h: int,
        time_stop_closes_24h: int = 0,
    ) -> List[Dict[str, Any]]:
        """Подстройка выходов (positions.*) по статистике."""
        proposals: List[Dict[str, Any]] = []
        if virtual_n_24h >= 8 and virtual_wr_24h < 40:
            proposals.extend(
                [
                    {
                        "risk": "low",
                        "path": ["positions", "breakeven_after_pct"],
                        "delta": +0.04,
                        "summary": "Раньше breakeven — слабые виртуальные сделки",
                        "justification": f"virtual WR={virtual_wr_24h}%",
                    },
                    {
                        "risk": "low",
                        "path": ["positions", "trailing_distance_pct"],
                        "delta": -0.05,
                        "summary": "Уже трейлинг — меньше откатов",
                        "justification": f"virtual WR={virtual_wr_24h}%",
                    },
                ]
            )
        if virtual_n_24h >= 8 and virtual_wr_24h >= 55 and real_pnl_24h > 0:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["positions", "trailing_activation_pct"],
                    "delta": -0.05,
                    "summary": "Раньше трейлинг — виртуальные в плюсе",
                    "justification": f"virtual WR={virtual_wr_24h}% real PnL={real_pnl_24h}",
                }
            )
        if real_wr_24h < 38 and real_pnl_24h < -8:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["positions", "trailing_distance_atr_mult"],
                    "delta": -0.1,
                    "summary": "Ужесточить дистанцию SL (ATR) после просадки",
                    "justification": f"real WR={real_wr_24h}% PnL={real_pnl_24h}",
                }
            )
        if time_stop_closes_24h >= 3:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["positions", "breakeven_after_pct"],
                    "delta": -0.04,
                    "summary": "Чаще breakeven — много time-stop выходов",
                    "justification": f"time_stop closes={time_stop_closes_24h}",
                }
            )
        return proposals

    def _get_nested(self, data: Dict, path: Tuple[str, ...]) -> Any:
        cur: Any = data
        for key in path:
            cur = cur[key]
        return cur

    def _set_nested(self, data: Dict, path: Tuple[str, ...], value: Any) -> None:
        cur = data
        for key in path[:-1]:
            cur = cur.setdefault(key, {})
        cur[path[-1]] = value

    def apply_low_risk_proposal(self, proposal: Dict[str, Any], *, reload: bool = True) -> bool:
        path_tuple = tuple(proposal["path"])
        if path_tuple not in LOW_RISK_TUNING:
            return False
        lo, hi, step = LOW_RISK_TUNING[path_tuple]
        if not self.config_path.exists():
            return False
        backup = self.sandbox_dir / f"config_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.yaml"
        shutil.copy2(self.config_path, backup)
        with self.config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        current = float(self._get_nested(data, path_tuple))
        new_val = current + float(proposal.get("delta", 0))
        if path_tuple[1] == "max_consecutive_losses":
            new_val = int(round(new_val))
        elif path_tuple == ("quality_gate", "min_rr_ratio"):
            new_val = round(new_val, 2)
        elif path_tuple[0] == "positions":
            new_val = round(new_val, 2)
        else:
            new_val = round(new_val, 3)
        new_val = max(lo, min(hi, new_val))
        self._set_nested(data, path_tuple, new_val)
        with self.config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
        self._log_change(
            {
                "risk": "low",
                "applied": True,
                "summary": proposal.get("summary", ""),
                "justification": proposal.get("justification", ""),
                "path": list(path_tuple),
                "old": current,
                "new": new_val,
                "backup": str(backup),
            }
        )
        if self.git_auto_commit and (self.root / ".git").exists():
            try:
                subprocess.run(
                    ["git", "add", str(self.config_path)],
                    cwd=self.root,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"auto-tune: {proposal.get('summary', 'config')}",
                    ],
                    cwd=self.root,
                    check=True,
                    capture_output=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
        if reload and self.reload_after_apply and self._on_reload:
            self._on_reload()
        return True

    def queue_critical_patch(self, patch_summary: str, justification: str, files: Dict[str, str]) -> None:
        """files: relative_path -> new content (только в sandbox)."""
        pending: List[Dict] = []
        if self.pending_path.exists():
            pending = json.loads(self.pending_path.read_text(encoding="utf-8"))
        patch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        for rel, content in files.items():
            dest = self.sandbox_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        entry = {
            "id": patch_id,
            "risk": "critical",
            "summary": patch_summary,
            "justification": justification,
            "files": list(files.keys()),
            "status": "pending_approval",
        }
        pending.append(entry)
        self.pending_path.write_text(json.dumps(pending, indent=2), encoding="utf-8")
        self._log_change({**entry, "applied": False})

    @staticmethod
    def _resolve_conflicting_proposals(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Один ключ config — одно изменение за цикл (приоритет ужесточению при убытке)."""
        by_path: Dict[tuple, List[Dict[str, Any]]] = {}
        for p in proposals:
            if p.get("risk") != "low":
                continue
            path = tuple(p.get("path") or [])
            if path:
                by_path.setdefault(path, []).append(p)

        resolved: List[Dict[str, Any]] = []
        for path, group in by_path.items():
            if len(group) == 1:
                resolved.append(group[0])
                continue
            deltas = [float(x.get("delta", 0)) for x in group]
            if all(d > 0 for d in deltas) or all(d < 0 for d in deltas):
                merged = dict(group[0])
                merged["delta"] = sum(deltas)
                resolved.append(merged)
                continue
            # Противоречие: оставляем ужесточение (положительный delta для confidence/cooldown)
            tighten = [x for x in group if float(x.get("delta", 0)) > 0]
            resolved.append(tighten[0] if tighten else group[0])

        for p in proposals:
            if p.get("risk") != "low":
                resolved.append(p)
        return resolved

    def process_proposals(
        self,
        proposals: List[Dict[str, Any]],
        *,
        reload: bool = False,
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        proposals = self._resolve_conflicting_proposals(proposals)
        applied: List[Dict[str, Any]] = []
        for p in proposals:
            if p.get("risk") == "low" and self.auto_low:
                if self.apply_low_risk_proposal(p, reload=False):
                    applied.append(p)
            elif p.get("risk") == "critical" and self.require_approval:
                self.queue_critical_patch(
                    p.get("summary", ""),
                    p.get("justification", ""),
                    p.get("files", {}),
                )
        if applied:
            if reload and self.reload_after_apply and self._on_reload:
                self._on_reload()
            else:
                self._pending_reload = True
        return applied

    def flush_reload(self) -> bool:
        """Один reload после пакета правок (конец цикла бота)."""
        if not self._pending_reload or not self.reload_after_apply or not self._on_reload:
            self._pending_reload = False
            return False
        self._pending_reload = False
        self._on_reload()
        return True

    def rollback_last_config(self) -> Optional[str]:
        backups = sorted(self.sandbox_dir.glob("config_backup_*.yaml"))
        if not backups:
            return None
        latest = backups[-1]
        shutil.copy2(latest, self.config_path)
        self._log_change(
            {
                "risk": "low",
                "applied": True,
                "summary": "Откат config.yaml из резервной копии",
                "justification": str(latest),
                "rollback": True,
            }
        )
        if self.reload_after_apply and self._on_reload:
            self._on_reload()
        return str(latest)
