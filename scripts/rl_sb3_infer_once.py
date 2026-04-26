#!/usr/bin/env python3
"""
Один шаг инференса SB3 на текущем наблюдении MetaTradingEnv (без торговли).

  python scripts/rl_sb3_infer_once.py
  python scripts/rl_sb3_infer_once.py --model models/rl_meta_sb3_ppo.zip --seed 0

Действие 0..3 совпадает с ACTION_RISK_MULT в engine.rl_meta_controller.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _resolve_zip(path: Path, root: Path) -> Path:
    p = path if path.is_absolute() else root / path
    if p.suffix.lower() != ".zip":
        p = Path(str(p) + ".zip")
    return p


def main() -> None:
    try:
        from stable_baselines3 import DQN, PPO
    except ImportError as e:
        raise SystemExit("pip install -r requirements-rl.txt\n" + str(e)) from e

    ap = argparse.ArgumentParser(description="SB3 one-shot predict on MetaTradingEnv")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--model", type=Path, default=None, help="Путь к .zip (по умолчанию rl_sb3.save_path)")
    ap.add_argument("--algorithm", choices=("PPO", "DQN"), default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--deterministic", action="store_true", default=True)
    ap.add_argument("--stochastic", action="store_true", help="sample из политики")
    args = ap.parse_args()

    deterministic = not args.stochastic

    from core.config import BotConfig
    from engine.rl_meta_controller import ACTION_RISK_MULT
    from rl_env.klines_loader import load_klines_for_training
    from rl_env.meta_trading_env import MetaTradingEnvConfig
    from rl_env.sb3_env import SB3MetaTradingEnv

    cfg = BotConfig.load(str(args.config))
    section = cfg.get("rl_sb3", default={}) or {}

    raw_save = args.model or Path(str(section.get("save_path", "models/rl_meta_sb3_ppo")))
    model_path = _resolve_zip(raw_save, ROOT)
    if not model_path.is_file():
        raise SystemExit(f"Файл модели не найден: {model_path}")

    algo = (args.algorithm or section.get("algorithm", "PPO") or "PPO").upper()
    if algo == "DQN":
        model = DQN.load(str(model_path))
    else:
        model = PPO.load(str(model_path))

    klines = load_klines_for_training(cfg, args.config)
    env_yaml = section.get("env") or {}
    mcfg = MetaTradingEnvConfig(
        sequence_length=int(
            env_yaml.get("sequence_length", cfg.get("bot", "feature_window", default=128))
        ),
        fee_per_turnover=float(env_yaml.get("fee_per_turnover", 0.0006)),
        max_episode_steps=int(env_yaml.get("max_episode_steps", 512)),
        drawdown_penalty_coef=float(env_yaml.get("drawdown_penalty_coef", 0.5)),
    )
    env = SB3MetaTradingEnv(klines, mcfg)

    obs, info = env.reset(seed=args.seed)
    action, _states = model.predict(obs, deterministic=deterministic)
    a = int(action)
    out = {
        "action": a,
        "risk_multiplier": float(ACTION_RISK_MULT.get(a % 4, 1.0)),
        "deterministic": deterministic,
        "model": str(model_path),
        "algorithm": algo,
        "obs_dim": int(len(obs)),
        "reset_info": info,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
