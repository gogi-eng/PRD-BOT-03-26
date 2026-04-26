"""
RL Meta-Controller: DQN (опционально torch) + rule-based с тем же API.
Источник: +Gemma.txt
"""
from __future__ import annotations

import random
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, List, Tuple

# Действия: 0 NO_TRADE, 1 LOW, 2 NORMAL, 3 AGGRESSIVE
ACTION_RISK_MULT: dict[int, float] = {0: 0.0, 1: 0.5, 2: 1.0, 3: 1.8}

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim

    _TORCH = True
except Exception:
    _TORCH = False
    torch = None  # type: ignore
    nn = None  # type: ignore
    optim = None  # type: ignore


def _as_cfg_dict(cfg: Any) -> dict:
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        return cfg
    raw = getattr(cfg, "raw", None)
    if isinstance(raw, dict):
        return raw
    return {}


def _as_float_list(x: Any) -> List[float]:
    if not x:
        return [0.0] * 7
    if isinstance(x, (list, tuple)):
        o = [float(v) for v in x]
        if len(o) < 7:
            o = o + [0.0] * (7 - len(o))
        return o[:7]
    if hasattr(x, "tolist"):
        o = [float(v) for v in x.tolist()][:7]  # type: ignore[union-attr]
    else:
        o = [float(x)] + [0.0] * 6
    if len(o) < 7:
        o = o + [0.0] * (7 - len(o))
    return o[:7]


if _TORCH and nn is not None and torch is not None and optim is not None:

    class _MetaDQNNet(nn.Module):
        def __init__(self, state_size: int = 7, action_size: int = 4) -> None:
            super().__init__()
            self._net = nn.Sequential(
                nn.Linear(state_size, 32),
                nn.ReLU(),
                nn.Linear(32, action_size),
            )

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            return self._net(x)

    class RLDQNController:
        """DQN + replay; train() вызывать снаружи с периодом."""

        def __init__(
            self,
            state_size: int = 7,
            action_size: int = 4,
            lr: float = 0.0005,
            gamma: float = 0.9,
        ) -> None:
            self.state_size = state_size
            self.action_size = action_size
            self.gamma = gamma
            self.memory: Deque[Tuple] = deque(maxlen=10_000)
            self.model = _MetaDQNNet(state_size, action_size)
            self.optimizer = optim.Adam(self.model.parameters(), lr=lr)  # type: ignore[union-attr, misc]
            self.epsilon = 0.1

        def _tensor(self, st: List[float]) -> "torch.Tensor":
            return torch.FloatTensor(st).view(1, -1)  # type: ignore[union-attr, misc, no-any-return]

        def act(self, state: object) -> int:
            st = _as_float_list(state)
            if random.random() < self.epsilon:
                return int(random.randrange(0, self.action_size))
            with torch.no_grad():
                t = self._tensor(st)
                return int(self.model(t).argmax(1).item())  # type: ignore[union-attr, misc, no-untyped-call]

        def remember(self, s: object, a: int, r: float, s_next: object) -> None:
            self.memory.append(
                (
                    _as_float_list(s),
                    int(a),
                    float(r),
                    _as_float_list(s_next),
                )
            )

        def train(self) -> None:
            if len(self.memory) < 32:  # type: ignore[arg-type, misc]
                return
            batch = random.sample(self.memory, min(32, len(self.memory)))
            s_b = torch.FloatTensor([b[0] for b in batch])  # type: ignore[call-arg, misc]
            a_b = torch.LongTensor([b[1] for b in batch]).view(-1, 1)  # type: ignore[call-arg, misc]
            r_b = torch.FloatTensor([b[2] for b in batch])  # type: ignore[call-arg, misc]
            sn_b = torch.FloatTensor([b[3] for b in batch])  # type: ignore[call-arg, misc]
            with torch.no_grad():
                nq = self.model(sn_b).max(1)[0]  # type: ignore[operator, no-untyped-call, misc]
            target = r_b + self.gamma * nq
            qv = self.model(s_b).gather(1, a_b)  # type: ignore[no-untyped-call, misc, union-attr, operator]
            loss = nn.MSELoss()(qv.squeeze(1), target)  # type: ignore[no-untyped-call, misc, union-attr]
            self.optimizer.zero_grad()  # type: ignore[no-untyped-call, misc, union-attr]
            loss.backward()  # type: ignore[no-untyped-call, union-attr]
            self.optimizer.step()  # type: ignore[no-untyped-call, misc, union-attr]

        @staticmethod
        def get_risk_multiplier_for_action(a: int) -> float:
            return float(ACTION_RISK_MULT.get(int(a) % 4, 1.0))

else:  # pragma: no cover

    class RLDQNController:  # type: ignore[no-redef]
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("torch required for RLDQNController")

        def act(self, state: object) -> int:  # noqa: ARG002
            return 2

        def remember(self, s: object, a: int, r: float, s_next: object) -> None:  # noqa: ARG002
            pass

        def train(self) -> None:
            pass

        @staticmethod
        def get_risk_multiplier_for_action(a: int) -> float:
            return float(ACTION_RISK_MULT.get(int(a) % 4, 1.0))


@dataclass
class RuleRLMetaController:
    """Контроллер без torch: по риску/просадке/волатильности в state."""

    last_action: int = 2
    memory: list = field(default_factory=list)  # для совместимости с remember
    epsilon: float = 0.0

    def act(self, state: object) -> int:
        st = _as_float_list(state)
        _wr, dr, vol = st[0], st[2], st[3]
        pnl_leg = st[1]
        if pnl_leg < -0.2 or dr > 0.12 or vol > 0.4:
            self.last_action = 0
        elif dr > 0.06 or st[0] < 0.35:
            self.last_action = 1
        elif st[0] > 0.6 and dr < 0.03 and vol < 0.2:
            self.last_action = 3
        else:
            self.last_action = 2
        return int(self.last_action)

    def remember(self, s: object, a: int, r: float, s_next: object) -> None:  # noqa: ARG002
        self.memory.append(1.0)
        if len(self.memory) > 5000:
            self.memory = self.memory[-2000:]

    def train(self) -> None:
        pass

    @staticmethod
    def get_risk_multiplier_for_action(a: int) -> float:
        return float(ACTION_RISK_MULT.get(int(a) % 4, 1.0))


def build_rl_meta_from_config(cfg: Any) -> object:
    d = _as_cfg_dict(cfg).get("rl_meta") or {}
    use_torch = bool(d.get("use_torch", False)) and _TORCH
    if use_torch:
        try:
            return RLDQNController(
                state_size=7,
                action_size=4,
                lr=float(d.get("lr", 0.0005)),
                gamma=float(d.get("gamma", 0.9)),
            )
        except Exception:
            return RuleRLMetaController()
    return RuleRLMetaController()


def state_from_meta_ohlcv(
    meta_drawdown: float,
    last_pnl: float,
    last_signal_conf: float,
    vol_closes: List[float],
    regime: str,
    win_rate_hint: float = 0.5,
) -> List[float]:
    """Семимерный state: winrate, pnl, drawdown, vol_norm, trend_regime, conf, 0."""
    vol = 0.0
    if len(vol_closes) > 2:
        rets = [
            (vol_closes[i] - vol_closes[i - 1]) / (vol_closes[i - 1] + 1e-9)
            for i in range(1, len(vol_closes))
        ]
        if rets and len(rets) > 1:
            try:
                vol = float(statistics.pstdev(rets))
            except Exception:
                vol = 0.0
    tr = 0.5
    if regime == "TREND":
        tr = 0.75
    elif regime == "CHAOS":
        tr = 0.9
    elif regime == "RANGE":
        tr = 0.25
    # vol (σ) ~0…0.05 на крипте → нормируем в 0..1
    vol_norm = min(1.0, max(0.0, float(vol) * 25.0))
    return [
        min(1.0, max(0.0, float(win_rate_hint))),
        float(last_pnl),
        float(min(1.0, max(0.0, meta_drawdown))),
        float(vol_norm),
        float(tr),
        float(min(1.0, max(0.0, last_signal_conf / 100.0))),
        0.0,
    ]


@dataclass
class RLMetaControllerFacade:
    """Точка входа: DQN при torch+use_torch, иначе rule-based."""

    inner: object = field(default_factory=RuleRLMetaController)

    @classmethod
    def from_config(cls, cfg: Any) -> "RLMetaControllerFacade":
        d = _as_cfg_dict(cfg).get("rl_meta") or {}
        if bool(d.get("use_torch", False)) and _TORCH:
            try:
                return cls(
                    inner=RLDQNController(
                        state_size=7,
                        action_size=4,
                        lr=float(d.get("lr", 0.0005)),
                        gamma=float(d.get("gamma", 0.9)),
                    )
                )
            except Exception:
                return cls(inner=RuleRLMetaController())
        return cls(inner=RuleRLMetaController())

    def act(self, state: object) -> int:
        return int(self.inner.act(state))  # type: ignore[union-attr, no-untyped-call, arg-type]

    def remember(self, s: object, a: int, r: float, s_next: object) -> None:
        return self.inner.remember(s, a, r, s_next)  # type: ignore[union-attr, no-untyped-call, arg-type]

    def train(self) -> None:
        return self.inner.train()  # type: ignore[union-attr, no-untyped-call]

    @staticmethod
    def get_risk_multiplier_for_action(a: int) -> float:
        return float(ACTION_RISK_MULT.get(int(a) % 4, 1.0))
