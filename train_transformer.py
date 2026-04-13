#!/usr/bin/env python3
"""Train compact transformer on backtest trades.

Key fixes:
1) BCEWithLogitsLoss + pos_weight for imbalance
2) Smaller transformer (few thousand params)
3) Stratified split + weighted sampler + optional win augmentation
4) Best checkpoint selected by precision on class "win"

Usage:
    # 1) From repo root, collect enough trades (need >=20 labeled win/loss rows):
    python backtester.py --all-whitelist --days 45 --interval 15
    # 2) Train (writes transformer_weights.pt next to this script):
    python train_transformer.py --data training_data.json --epochs 220

    training_data.json is created by backtester in the current working directory (use repo root for both).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TRAIN] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("TRAIN")

BOT_DIR = Path(__file__).parent.resolve()

# (field_name, min_value, max_value, default)
FEATURE_SPECS = [
    ("composite_score", 0.0, 1.0, 0.5),
    ("trend_score", 0.0, 1.0, 0.5),
    ("orderflow_score", 0.0, 1.0, 0.5),
    ("ai_score", 0.0, 1.0, 0.5),
    ("normalized_imbalance", -1.0, 1.0, 0.0),
    ("rr_ratio_norm", 0.0, 1.0, 0.15),
    ("htf_4h_trend_norm", 0.0, 1.0, 0.5),
]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def set_seed(seed: int):
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


class TradeDataset(Dataset):
    def __init__(self, features: list[list[float]], labels: list[float]):
        self.features = torch.tensor(features, dtype=torch.float32)
        self.labels = torch.tensor(labels, dtype=torch.float32)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.features[idx], self.labels[idx]


class TinyTransformerClassifier(nn.Module):
    """Tiny transformer over feature tokens (one token per feature)."""

    FEATURE_DIM = len(FEATURE_SPECS)

    def __init__(self, d_model: int = 16, nhead: int = 2, num_layers: int = 1, dropout: float = 0.2):
        super().__init__()
        self.scalar_proj = nn.Linear(1, d_model)
        self.pos_embedding = nn.Parameter(torch.zeros(1, self.FEATURE_DIM, d_model))
        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=d_model * 2,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(enc_layer, num_layers=num_layers)
        self.head = nn.Sequential(
            nn.LayerNorm(d_model),
            nn.Linear(d_model, d_model // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [B, F]
        tokens = x.unsqueeze(-1)  # [B, F, 1]
        embeds = self.scalar_proj(tokens) + self.pos_embedding
        encoded = self.encoder(embeds)
        pooled = encoded.mean(dim=1)
        return self.head(pooled).squeeze(-1)


def trade_to_features(trade: dict) -> list[float]:
    rr_ratio = float(trade.get("rr_ratio", 2.0) or 2.0)
    rr_ratio_norm = _clamp(rr_ratio / 15.0, 0.0, 1.0)

    htf_4h_trend = float(trade.get("htf_4h_trend", 0) or 0)
    htf_4h_trend_norm = _clamp((htf_4h_trend + 1.0) / 2.0, 0.0, 1.0)

    raw = {
        "composite_score": float(trade.get("composite_score", 0.5) or 0.5),
        "trend_score": float(trade.get("trend_score", 0.5) or 0.5),
        "orderflow_score": float(trade.get("orderflow_score", 0.5) or 0.5),
        "ai_score": float(trade.get("ai_score", 0.5) or 0.5),
        "normalized_imbalance": float(trade.get("normalized_imbalance", 0.0) or 0.0),
        "rr_ratio_norm": rr_ratio_norm,
        "htf_4h_trend_norm": htf_4h_trend_norm,
    }

    return [_clamp(raw[name], lo, hi) for name, lo, hi, _ in FEATURE_SPECS]


def load_dataset(data_path: str) -> tuple[list[list[float]], list[float]]:
    with open(data_path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    features: list[list[float]] = []
    labels: list[float] = []
    for trade in rows:
        result = str(trade.get("result", "")).lower()
        if result not in {"win", "loss"}:
            continue
        features.append(trade_to_features(trade))
        labels.append(1.0 if result == "win" else 0.0)

    return features, labels


def augment_wins(
    features: list[list[float]],
    labels: list[float],
    factor: int,
    noise_std: float,
    target_pos_count: int | None = None,
) -> tuple[list[list[float]], list[float]]:
    if factor <= 1:
        return list(features), list(labels)

    win_indices = [i for i, label in enumerate(labels) if label > 0.5]
    if not win_indices:
        return list(features), list(labels)

    out_x = list(features)
    out_y = list(labels)
    bounds = [(lo, hi) for _, lo, hi, _ in FEATURE_SPECS]

    for _ in range(factor - 1):
        for idx in win_indices:
            if target_pos_count is not None and sum(1 for y in out_y if y > 0.5) >= target_pos_count:
                return out_x, out_y
            original = features[idx]
            noisy = []
            for value, (lo, hi) in zip(original, bounds):
                noisy.append(_clamp(value + random.gauss(0.0, noise_std), lo, hi))
            out_x.append(noisy)
            out_y.append(1.0)

    return out_x, out_y


def stratified_split(
    features: list[list[float]], labels: list[float], val_ratio: float, seed: int
) -> tuple[list[list[float]], list[float], list[list[float]], list[float]]:
    pos_idx = [i for i, value in enumerate(labels) if value > 0.5]
    neg_idx = [i for i, value in enumerate(labels) if value <= 0.5]

    if len(pos_idx) < 2 or len(neg_idx) < 2:
        raise ValueError(
            f"Not enough class diversity for stratified split: wins={len(pos_idx)} losses={len(neg_idx)}"
        )

    rnd = random.Random(seed)
    rnd.shuffle(pos_idx)
    rnd.shuffle(neg_idx)

    n_pos_val = max(1, int(round(len(pos_idx) * val_ratio)))
    n_neg_val = max(1, int(round(len(neg_idx) * val_ratio)))
    n_pos_val = min(n_pos_val, len(pos_idx) - 1)
    n_neg_val = min(n_neg_val, len(neg_idx) - 1)

    val_idx = pos_idx[:n_pos_val] + neg_idx[:n_neg_val]
    train_idx = pos_idx[n_pos_val:] + neg_idx[n_neg_val:]
    rnd.shuffle(train_idx)
    rnd.shuffle(val_idx)

    train_x = [features[i] for i in train_idx]
    train_y = [labels[i] for i in train_idx]
    val_x = [features[i] for i in val_idx]
    val_y = [labels[i] for i in val_idx]
    return train_x, train_y, val_x, val_y


def build_weighted_sampler(labels: list[float]) -> WeightedRandomSampler:
    pos_count = sum(1 for y in labels if y > 0.5)
    neg_count = max(len(labels) - pos_count, 1)
    pos_count = max(pos_count, 1)

    sample_weights = []
    for label in labels:
        sample_weights.append(1.0 / (pos_count if label > 0.5 else neg_count))

    return WeightedRandomSampler(
        weights=torch.tensor(sample_weights, dtype=torch.double),
        num_samples=len(sample_weights),
        replacement=True,
    )


@dataclass
class Metrics:
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float
    recall: float
    f1: float
    accuracy: float


def compute_metrics(logits: torch.Tensor, labels: torch.Tensor, decision_threshold: float) -> Metrics:
    probs = torch.sigmoid(logits)
    preds = (probs >= decision_threshold).int()
    targets = labels.int()

    tp = int(((preds == 1) & (targets == 1)).sum().item())
    fp = int(((preds == 1) & (targets == 0)).sum().item())
    tn = int(((preds == 0) & (targets == 0)).sum().item())
    fn = int(((preds == 0) & (targets == 1)).sum().item())

    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-8)
    accuracy = (tp + tn) / max(tp + fp + tn + fn, 1)

    return Metrics(tp=tp, fp=fp, tn=tn, fn=fn, precision=precision, recall=recall, f1=f1, accuracy=accuracy)


def select_best_threshold(
    logits: torch.Tensor,
    labels: torch.Tensor,
    default_threshold: float,
) -> tuple[float, Metrics]:
    if len(labels) == 0:
        empty = Metrics(tp=0, fp=0, tn=0, fn=0, precision=0.0, recall=0.0, f1=0.0, accuracy=0.0)
        return default_threshold, empty

    best_threshold = default_threshold
    best_metrics = compute_metrics(logits, labels, default_threshold)
    total = len(labels)
    min_pred_pos = max(2, int(total * 0.02))

    for step in range(30, 81, 2):
        threshold = step / 100.0
        metrics = compute_metrics(logits, labels, threshold)
        pred_pos = metrics.tp + metrics.fp
        if metrics.tp == 0 or pred_pos < min_pred_pos:
            continue

        better = (
            metrics.precision > best_metrics.precision + 1e-8
            or (
                abs(metrics.precision - best_metrics.precision) <= 1e-8
                and metrics.f1 > best_metrics.f1 + 1e-8
            )
        )
        if better:
            best_threshold = threshold
            best_metrics = metrics

    return best_threshold, best_metrics


def evaluate(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
    decision_threshold: float,
) -> tuple[float, Metrics, torch.Tensor, torch.Tensor]:
    model.eval()
    total_loss = 0.0
    total = 0
    all_logits = []
    all_labels = []

    with torch.no_grad():
        for feats, labs in loader:
            feats = feats.to(device)
            labs = labs.to(device)
            logits = model(feats)
            loss = criterion(logits, labs)

            batch_size = len(labs)
            total_loss += loss.item() * batch_size
            total += batch_size

            all_logits.append(logits.cpu())
            all_labels.append(labs.cpu())

    merged_logits = torch.cat(all_logits) if all_logits else torch.zeros(0)
    merged_labels = torch.cat(all_labels) if all_labels else torch.zeros(0)
    metrics = compute_metrics(merged_logits, merged_labels, decision_threshold)
    return total_loss / max(total, 1), metrics, merged_logits, merged_labels


def train(
    data_path: str,
    epochs: int,
    lr: float,
    batch_size: int,
    output_path: str,
    val_ratio: float,
    decision_threshold: float,
    seed: int,
    augment_wins_factor: int,
    augment_noise_std: float,
    min_trades: int = 20,
) -> bool:
    set_seed(seed)

    features, labels = load_dataset(data_path)
    if len(features) < min_trades:
        logger.error(
            f"Not enough labeled trades: {len(features)} (need at least {min_trades}). "
            "Each row must have result \"win\" or \"loss\" (from backtest exits)."
        )
        logger.info(
            "Collect more data from repo root, e.g.:\n"
            "  python backtester.py --all-whitelist --days 60 --interval 15\n"
            "Or single symbol:  python backtester.py --symbol ETHUSDT --days 90 --interval 15\n"
            "Then:  python train_transformer.py --data training_data.json"
        )
        return False

    wins = sum(1 for y in labels if y > 0.5)
    losses = len(labels) - wins
    logger.info(f"Raw dataset: total={len(labels)} wins={wins} losses={losses}")

    try:
        train_x_raw, train_y_raw, val_x, val_y = stratified_split(features, labels, val_ratio=val_ratio, seed=seed)
    except ValueError as exc:
        logger.error(str(exc))
        return False

    train_wins_raw = sum(1 for y in train_y_raw if y > 0.5)
    train_losses_raw = len(train_y_raw) - train_wins_raw

    target_aug_wins = max(train_wins_raw, int(train_losses_raw * 0.95))
    train_x, train_y = augment_wins(
        train_x_raw,
        train_y_raw,
        factor=augment_wins_factor,
        noise_std=augment_noise_std,
        target_pos_count=target_aug_wins,
    )

    wins_aug = sum(1 for y in train_y if y > 0.5)
    losses_aug = len(train_y) - wins_aug
    logger.info(
        f"After train-only augmentation: train={len(train_y)} wins={wins_aug} losses={losses_aug} "
        f"(target_wins={target_aug_wins})"
    )

    train_wins = sum(1 for y in train_y if y > 0.5)
    train_losses = len(train_y) - train_wins
    val_wins = sum(1 for y in val_y if y > 0.5)
    val_losses = len(val_y) - val_wins
    logger.info(
        f"Split | train={len(train_y)} (win={train_wins}, loss={train_losses}) "
        f"val={len(val_y)} (win={val_wins}, loss={val_losses})"
    )

    train_ds = TradeDataset(train_x, train_y)
    val_ds = TradeDataset(val_x, val_y)

    sampler = build_weighted_sampler(train_y)
    train_loader = DataLoader(train_ds, batch_size=batch_size, sampler=sampler)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = TinyTransformerClassifier(d_model=16, nhead=2, num_layers=1, dropout=0.2).to(device)

    total_params = sum(p.numel() for p in model.parameters())
    logger.info(f"Model params: {total_params:,} | device={device}")

    pos_weight_value = max(train_losses_raw / max(train_wins_raw, 1), 1.0)
    pos_weight_tensor = torch.tensor([pos_weight_value], dtype=torch.float32, device=device)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight_tensor)

    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-3)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.7, patience=12, min_lr=1e-5)

    best_precision = -1.0
    best_f1 = -1.0
    best_val_loss = float("inf")
    best_epoch = 0
    no_improve = 0
    patience = 40

    for epoch in range(1, epochs + 1):
        model.train()
        epoch_loss = 0.0
        seen = 0
        for feats, labs in train_loader:
            feats = feats.to(device)
            labs = labs.to(device)

            optimizer.zero_grad()
            logits = model(feats)
            loss = criterion(logits, labs)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            bs = len(labs)
            epoch_loss += loss.item() * bs
            seen += bs

        train_loss = epoch_loss / max(seen, 1)
        val_loss, val_metrics, val_logits, val_labels = evaluate(model, val_loader, criterion, device, decision_threshold)
        tuned_threshold, tuned_metrics = select_best_threshold(val_logits, val_labels, decision_threshold)
        scheduler.step(val_loss)

        eval_metrics = tuned_metrics if (tuned_metrics.tp + tuned_metrics.fp) > 0 else val_metrics
        eval_threshold = tuned_threshold if (tuned_metrics.tp + tuned_metrics.fp) > 0 else decision_threshold

        if epoch == 1 or epoch % 10 == 0:
            logger.info(
                f"Epoch {epoch:3d}/{epochs} | train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
                f"P(win)={eval_metrics.precision:.3f} R={eval_metrics.recall:.3f} F1={eval_metrics.f1:.3f} "
                f"Acc={eval_metrics.accuracy * 100:.1f}% thr={eval_threshold:.2f} "
                f"(TP={eval_metrics.tp} FP={eval_metrics.fp} TN={eval_metrics.tn} FN={eval_metrics.fn})"
            )

        improved = (
            eval_metrics.precision > best_precision + 1e-8
            or (
                abs(eval_metrics.precision - best_precision) <= 1e-8
                and eval_metrics.f1 > best_f1 + 1e-8
            )
            or (
                abs(eval_metrics.precision - best_precision) <= 1e-8
                and abs(eval_metrics.f1 - best_f1) <= 1e-8
                and val_loss < best_val_loss - 1e-8
            )
        )

        if improved:
            best_precision = eval_metrics.precision
            best_f1 = eval_metrics.f1
            best_val_loss = val_loss
            best_epoch = epoch
            no_improve = 0
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "epoch": epoch,
                    "feature_dim": TinyTransformerClassifier.FEATURE_DIM,
                    "feature_keys": [name for name, _, _, _ in FEATURE_SPECS],
                    "val_precision": eval_metrics.precision,
                    "val_recall": eval_metrics.recall,
                    "val_f1": eval_metrics.f1,
                    "val_accuracy": eval_metrics.accuracy,
                    "val_tp": eval_metrics.tp,
                    "val_fp": eval_metrics.fp,
                    "val_tn": eval_metrics.tn,
                    "val_fn": eval_metrics.fn,
                    "decision_threshold": float(eval_threshold),
                    "loss": "BCEWithLogitsLoss",
                    "pos_weight": float(pos_weight_value),
                    "d_model": 16,
                    "nhead": 2,
                    "num_layers": 1,
                    "augment_wins_factor": augment_wins_factor,
                },
                output_path,
            )
        else:
            no_improve += 1

        if no_improve >= patience:
            logger.info(f"Early stopping at epoch {epoch} (no precision improvement for {patience} epochs)")
            break

    if not os.path.exists(output_path):
        logger.error("Training finished but no checkpoint was saved")
        return False

    best_ckpt = torch.load(output_path, map_location="cpu")
    logger.info("=" * 60)
    logger.info(f"Best checkpoint epoch: {best_epoch}")
    logger.info(f"Val precision (win): {best_ckpt.get('val_precision', 0.0):.3f}")
    logger.info(f"Val recall (win):    {best_ckpt.get('val_recall', 0.0):.3f}")
    logger.info(f"Val F1 (win):        {best_ckpt.get('val_f1', 0.0):.3f}")
    logger.info(f"Val accuracy:        {best_ckpt.get('val_accuracy', 0.0) * 100:.1f}%")
    logger.info(
        f"Confusion matrix: TP={best_ckpt.get('val_tp', 0)} FP={best_ckpt.get('val_fp', 0)} "
        f"TN={best_ckpt.get('val_tn', 0)} FN={best_ckpt.get('val_fn', 0)}"
    )
    logger.info(f"Saved weights: {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Train tiny transformer for win/loss classification")
    parser.add_argument("--data", type=str, default=str(BOT_DIR / "training_data.json"))
    parser.add_argument("--epochs", type=int, default=220)
    parser.add_argument("--lr", type=float, default=0.002)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--decision-threshold", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--augment-wins-factor", type=int, default=2)
    parser.add_argument("--augment-noise-std", type=float, default=0.03)
    parser.add_argument("--output", type=str, default=str(BOT_DIR / "transformer_weights.pt"))
    parser.add_argument(
        "--min-trades",
        type=int,
        default=20,
        help="Minimum labeled trades (win+loss) required; below this training aborts.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.data):
        logger.error(f"Training data not found: {args.data}")
        logger.info(
            "Run backtest from the same directory where you want training_data.json (usually repo root):\n"
            "  python backtester.py --symbol BTCUSDT --days 30 --interval 15\n"
            "Or: python train_transformer.py --data /full/path/to/training_data.json"
        )
        sys.exit(1)

    ok = train(
        data_path=args.data,
        epochs=args.epochs,
        lr=args.lr,
        batch_size=args.batch_size,
        output_path=args.output,
        val_ratio=args.val_ratio,
        decision_threshold=args.decision_threshold,
        seed=args.seed,
        augment_wins_factor=max(1, args.augment_wins_factor),
        augment_noise_std=max(0.0, args.augment_noise_std),
        min_trades=max(4, args.min_trades),
    )
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
