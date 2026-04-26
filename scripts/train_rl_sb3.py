#!/usr/bin/env python3
"""
Обучение meta-RL (PPO / DQN) на ``SB3MetaTradingEnv`` по секции ``rl_sb3`` в config.yaml.

  pip install -r requirements-rl.txt
  python scripts/train_rl_sb3.py
  python scripts/train_rl_sb3.py --config /path/config.yaml --force

Модель сохраняется в ``rl_sb3.save_path`` (.zip для SB3).
Интеграцию весов в продовый ``RLMetaControllerFacade`` делайте отдельно (экспорт политики / rule fallback).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    try:
        from stable_baselines3 import DQN, PPO
        from stable_baselines3.common.vec_env import DummyVecEnv
    except ImportError as e:
        raise SystemExit(
            "Нужен Stable-Baselines3: pip install -r requirements-rl.txt\n" + str(e)
        ) from e

    ap = argparse.ArgumentParser(description="Train SB3 on MetaTradingEnv (rl_sb3 config)")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--force", action="store_true", help="Обучать даже если rl_sb3.enabled: false")
    ap.add_argument("--timesteps", type=int, default=None, help="Переопределить total_timesteps")
    args = ap.parse_args()

    from core.config import BotConfig
    from rl_env.klines_loader import load_klines_for_training
    from rl_env.meta_trading_env import MetaTradingEnvConfig
    from rl_env.sb3_env import SB3MetaTradingEnv

    cfg = BotConfig.load(str(args.config))
    section = cfg.get("rl_sb3", default={}) or {}
    if not section.get("enabled", False) and not args.force:
        raise SystemExit(
            "rl_sb3.enabled: false. Включите в config.yaml или запустите с --force"
        )

    klines = load_klines_for_training(cfg, args.config)
    if len(klines) < 300:
        raise SystemExit(f"Слишком мало свечей: {len(klines)}")

    env_yaml = section.get("env") or {}
    feat_win = int(
        env_yaml.get("sequence_length", cfg.get("bot", "feature_window", default=128))
    )
    mcfg = MetaTradingEnvConfig(
        sequence_length=feat_win,
        fee_per_turnover=float(env_yaml.get("fee_per_turnover", 0.0006)),
        max_episode_steps=int(env_yaml.get("max_episode_steps", 512)),
        drawdown_penalty_coef=float(env_yaml.get("drawdown_penalty_coef", 0.5)),
    )

    n_envs = max(1, int(section.get("n_envs", 1)))

    def make_env():
        return SB3MetaTradingEnv(klines, mcfg)

    if n_envs == 1:
        vec = DummyVecEnv([make_env])
    else:
        vec = DummyVecEnv([make_env for _ in range(n_envs)])

    algo = str(section.get("algorithm", "PPO")).upper()
    policy = str(section.get("policy", "MlpPolicy"))
    lr = float(section.get("learning_rate", 3e-4))
    gamma = float(section.get("gamma", 0.99))
    tb = section.get("tensorboard_log") or None
    if tb:
        tb_path = Path(tb)
        if not tb_path.is_absolute():
            tb_path = ROOT / tb_path
        tb_path.mkdir(parents=True, exist_ok=True)
        tb = str(tb_path)

    steps = int(args.timesteps if args.timesteps is not None else section.get("total_timesteps", 50_000))

    if algo == "PPO":
        model = PPO(
            policy,
            vec,
            learning_rate=lr,
            gamma=gamma,
            n_steps=int(section.get("n_steps", 2048)),
            batch_size=int(section.get("batch_size", 64)),
            verbose=1,
            tensorboard_log=tb,
        )
    elif algo == "DQN":
        model = DQN(
            "MlpPolicy",
            vec,
            learning_rate=lr,
            gamma=gamma,
            buffer_size=int(section.get("buffer_size", 50_000)),
            learning_starts=int(section.get("learning_starts", 1000)),
            batch_size=int(section.get("batch_size", 32)),
            verbose=1,
            tensorboard_log=tb,
        )
    else:
        raise SystemExit(f"Unsupported rl_sb3.algorithm: {algo} (use PPO or DQN)")

    print(f"[rl_sb3] Training {algo} for {steps} steps, klines={len(klines)}")
    model.learn(total_timesteps=steps)

    raw_save = str(section.get("save_path", "models/rl_meta_sb3_ppo"))
    save_path = Path(raw_save)
    if not save_path.is_absolute():
        save_path = ROOT / save_path
    save_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(save_path))
    print(f"[rl_sb3] Saved to {save_path}.zip")


if __name__ == "__main__":
    main()
