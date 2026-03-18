#!/usr/bin/env python3
"""
TRANSFORMER TRAINING PIPELINE v2

Fixes:
- Focal Loss for class imbalance (not just pos_weight)
- Smaller model (~2K params for 133 samples)
- Data augmentation for minority class (wins)
- Proper train/val split with stratification

Usage:
    python train_transformer.py --data training_data.json --epochs 300
"""
import argparse
import json
import logging
import os
import random
import sys
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TRAIN] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("TRAIN")

BOT_DIR = Path(__file__).parent.resolve()


# ═══════════════════════════════════════════════════════════════
# MODEL: Compact MLP (right-sized for small datasets)
# ═══════════════════════════════════════════════════════════════

class TradePredictor(nn.Module):
    """Compact model for predicting trade win probability.

    ~2K parameters (vs 38K before) — appropriate for 100-500 samples.
    """
    FEATURE_DIM = 7

    def __init__(self, feature_dim=7):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(feature_dim, 16),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(16, 8),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(8, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)  # raw logits (no sigmoid)


# ═══════════════════════════════════════════════════════════════
# FOCAL LOSS — better for class imbalance than BCELoss
# ═══════════════════════════════════════════════════════════════

class FocalLoss(nn.Module):
    """Focal loss: down-weights easy examples, focuses on hard ones.

    With gamma=2, easy negatives (losses) contribute much less to gradient.
    alpha weights the positive class (wins) higher.
    """
    def __init__(self, alpha=0.7, gamma=2.0):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma

    def forward(self, logits, targets):
        probs = torch.sigmoid(logits)
        # For positive class (win): pt = probs, at = alpha
        # For negative class (loss): pt = 1-probs, at = 1-alpha
        pt = torch.where(targets == 1, probs, 1 - probs)
        at = torch.where(targets == 1, self.alpha, 1 - self.alpha)
        # Focal term: (1 - pt)^gamma
        focal_weight = (1 - pt) ** self.gamma
        # BCE component
        bce = -torch.where(
            targets == 1,
            torch.log(probs + 1e-8),
            torch.log(1 - probs + 1e-8),
        )
        loss = at * focal_weight * bce
        return loss.mean()


# ═══════════════════════════════════════════════════════════════
# DATASET with augmentation
# ═══════════════════════════════════════════════════════════════

class TradeDataset(Dataset):

    def __init__(self, features, labels):
        self.features = features
        self.labels = labels

    def __len__(self):
        return len(self.features)

    def __getitem__(self, idx):
        return torch.tensor(self.features[idx], dtype=torch.float32), \
               torch.tensor(self.labels[idx], dtype=torch.float32)


def load_and_augment(data_path: str, oversample_wins: int = 3, noise_std: float = 0.05):
    """Load training data with oversampling + noise augmentation for wins."""
    with open(data_path) as f:
        raw = json.load(f)

    features_win = []
    features_loss = []

    for trade in raw:
        result = trade.get("result", "")
        if result not in ("win", "loss"):
            continue

        feat = [
            float(trade.get("composite_score", 0.5)),
            float(trade.get("trend_score", 0.5)),
            float(trade.get("orderflow_score", 0.5)),
            float(trade.get("ai_score", 0.5)),
            float(trade.get("normalized_imbalance", 0.0)),
            min(float(trade.get("rr_ratio", 2.0)) / 15.0, 1.0),  # normalize RR to [0,1]
            float(trade.get("htf_4h_trend", 0)) * 0.5 + 0.5,  # map {-1,0,1} → {0, 0.5, 1}
        ]

        if result == "win":
            features_win.append(feat)
        else:
            features_loss.append(feat)

    logger.info(f"Raw data: {len(features_win)} wins, {len(features_loss)} losses")

    # Oversample wins with noise augmentation
    augmented_wins = list(features_win)  # originals
    for _ in range(oversample_wins - 1):
        for feat in features_win:
            noisy = [v + random.gauss(0, noise_std) for v in feat]
            # Clamp to valid ranges
            noisy = [max(0.0, min(1.0, v)) for v in noisy]
            augmented_wins.append(noisy)

    # Build balanced-ish dataset
    all_features = augmented_wins + features_loss
    all_labels = [1.0] * len(augmented_wins) + [0.0] * len(features_loss)

    logger.info(f"After augmentation: {len(augmented_wins)} wins, {len(features_loss)} losses")

    # Shuffle
    combined = list(zip(all_features, all_labels))
    random.seed(42)
    random.shuffle(combined)
    all_features, all_labels = zip(*combined)

    return list(all_features), list(all_labels)


def stratified_split(features, labels, val_ratio=0.2):
    """Split maintaining class ratio in train and val."""
    wins_f, wins_l, losses_f, losses_l = [], [], [], []
    for f, l in zip(features, labels):
        if l > 0.5:
            wins_f.append(f)
            wins_l.append(l)
        else:
            losses_f.append(f)
            losses_l.append(l)

    n_val_wins = max(1, int(len(wins_f) * val_ratio))
    n_val_losses = max(1, int(len(losses_f) * val_ratio))

    val_f = wins_f[:n_val_wins] + losses_f[:n_val_losses]
    val_l = wins_l[:n_val_wins] + losses_l[:n_val_losses]
    train_f = wins_f[n_val_wins:] + losses_f[n_val_losses:]
    train_l = wins_l[n_val_wins:] + losses_l[n_val_losses:]

    return train_f, train_l, val_f, val_l


# ═══════════════════════════════════════════════════════════════
# TRAINING
# ═══════════════════════════════════════════════════════════════

def train(data_path: str, epochs: int = 300, lr: float = 0.003, batch_size: int = 32,
          output_path: str = None):

    if output_path is None:
        output_path = str(BOT_DIR / "transformer_weights.pt")

    # Load + augment
    features, labels = load_and_augment(data_path, oversample_wins=3, noise_std=0.05)
    if len(features) < 20:
        logger.error(f"Not enough data: {len(features)} samples")
        return

    # Stratified split
    train_f, train_l, val_f, val_l = stratified_split(features, labels)
    train_ds = TradeDataset(train_f, train_l)
    val_ds = TradeDataset(val_f, val_l)

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size)

    n_train_wins = sum(1 for l in train_l if l > 0.5)
    n_val_wins = sum(1 for l in val_l if l > 0.5)
    logger.info(f"Train: {len(train_ds)} ({n_train_wins} wins) | Val: {len(val_ds)} ({n_val_wins} wins)")

    # Model
    model = TradePredictor(feature_dim=TradePredictor.FEATURE_DIM)
    total_params = sum(p.numel() for p in model.parameters())
    logger.info(f"Model: {total_params:,} parameters")

    # Focal loss (handles imbalance natively)
    criterion = FocalLoss(alpha=0.7, gamma=2.0)
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-3)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_val_f1 = 0.0
    best_epoch = 0
    patience = 50
    no_improve = 0

    for epoch in range(1, epochs + 1):
        # Train
        model.train()
        train_loss = 0.0
        train_tp = train_fp = train_tn = train_fn = 0

        for feats, labs in train_loader:
            optimizer.zero_grad()
            logits = model(feats)
            loss = criterion(logits, labs)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            train_loss += loss.item() * len(labs)
            preds = (torch.sigmoid(logits) > 0.5).float()
            for p, l in zip(preds, labs):
                if p == 1 and l == 1: train_tp += 1
                elif p == 1 and l == 0: train_fp += 1
                elif p == 0 and l == 0: train_tn += 1
                else: train_fn += 1

        scheduler.step()

        # Validate
        model.eval()
        val_tp = val_fp = val_tn = val_fn = 0

        with torch.no_grad():
            for feats, labs in val_loader:
                logits = model(feats)
                preds = (torch.sigmoid(logits) > 0.5).float()
                for p, l in zip(preds, labs):
                    if p == 1 and l == 1: val_tp += 1
                    elif p == 1 and l == 0: val_fp += 1
                    elif p == 0 and l == 0: val_tn += 1
                    else: val_fn += 1

        # F1 score (better metric than accuracy for imbalanced data)
        val_precision = val_tp / max(val_tp + val_fp, 1)
        val_recall = val_tp / max(val_tp + val_fn, 1)
        val_f1 = 2 * val_precision * val_recall / max(val_precision + val_recall, 1e-8)
        val_acc = (val_tp + val_tn) / max(val_tp + val_fp + val_tn + val_fn, 1) * 100

        train_acc = (train_tp + train_tn) / max(train_tp + train_fp + train_tn + train_fn, 1) * 100

        if epoch % 25 == 0 or epoch == 1:
            logger.info(f"Epoch {epoch:3d}/{epochs} | "
                        f"Train acc={train_acc:.1f}% | "
                        f"Val acc={val_acc:.1f}% F1={val_f1:.3f} "
                        f"P={val_precision:.2f} R={val_recall:.2f} "
                        f"(TP={val_tp} FP={val_fp} TN={val_tn} FN={val_fn})")

        # Save best by F1 (not accuracy!)
        if val_f1 > best_val_f1:
            best_val_f1 = val_f1
            best_epoch = epoch
            no_improve = 0
            torch.save({
                'model_state_dict': model.state_dict(),
                'epoch': epoch,
                'val_f1': val_f1,
                'val_acc': val_acc,
                'val_precision': val_precision,
                'val_recall': val_recall,
                'feature_dim': TradePredictor.FEATURE_DIM,
            }, output_path)
        else:
            no_improve += 1

        if no_improve >= patience:
            logger.info(f"Early stopping at epoch {epoch}")
            break

    # Final report
    logger.info(f"{'=' * 50}")
    logger.info(f"Best model: epoch {best_epoch}")
    logger.info(f"  Val F1:        {best_val_f1:.3f}")

    if os.path.exists(output_path):
        ckpt = torch.load(output_path, weights_only=True)
        logger.info(f"  Val Accuracy:  {ckpt['val_acc']:.1f}%")
        logger.info(f"  Val Precision: {ckpt['val_precision']:.2f}")
        logger.info(f"  Val Recall:    {ckpt['val_recall']:.2f}")

    logger.info(f"Weights saved to {output_path}")
    return model


def main():
    parser = argparse.ArgumentParser(description="Train TradePredictor v2")
    parser.add_argument("--data", type=str, default=str(BOT_DIR / "training_data.json"))
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--lr", type=float, default=0.003)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--output", type=str, default=str(BOT_DIR / "transformer_weights.pt"))
    args = parser.parse_args()

    if not os.path.exists(args.data):
        logger.error(f"Training data not found: {args.data}")
        logger.info("Run: python backtester.py --all-whitelist --days 180 --threshold 0.35")
        return

    train(args.data, epochs=args.epochs, lr=args.lr,
          batch_size=args.batch_size, output_path=args.output)


if __name__ == "__main__":
    main()
