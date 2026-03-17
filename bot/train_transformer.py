#!/usr/bin/env python3
"""
TRANSFORMER TRAINING PIPELINE

Trains a PyTorch Transformer model on backtest data to predict trade outcomes.

Input: training_data.json (from backtester)
Output: trained model weights (transformer_weights.pt)

Usage:
    python train_transformer.py
    python train_transformer.py --data training_data.json --epochs 200 --lr 0.001
"""
import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader, random_split

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TRAIN] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("TRAIN")

BOT_DIR = Path(__file__).parent.resolve()


# ═══════════════════════════════════════════════════════════════
# MODEL: PriceTransformer (PyTorch)
# ═══════════════════════════════════════════════════════════════

class PriceTransformer(nn.Module):
    """Transformer model that predicts trade outcome from market features.

    Input: [composite_score, trend_score, orderflow_score, ai_score,
            normalized_imbalance, rr_ratio, htf_4h_trend]
    Output: probability of win (0-1)
    """
    FEATURE_DIM = 7

    def __init__(self, feature_dim=7, d_model=32, nhead=4, num_layers=3, dropout=0.1):
        super().__init__()
        self.input_proj = nn.Linear(feature_dim, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=d_model * 4,
            dropout=dropout, batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.head = nn.Sequential(
            nn.Linear(d_model, 16),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(16, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        # x: (batch, features) → (batch, 1, features) for transformer
        if x.dim() == 2:
            x = x.unsqueeze(1)
        x = self.input_proj(x)
        x = self.encoder(x)
        x = x.mean(dim=1)  # pool over sequence
        return self.head(x).squeeze(-1)


# ═══════════════════════════════════════════════════════════════
# DATASET
# ═══════════════════════════════════════════════════════════════

class TradeDataset(Dataset):
    """Dataset from backtest training_data.json."""

    SIDE_MAP = {"BUY": 1.0, "SELL": -1.0}

    def __init__(self, data_path: str):
        with open(data_path) as f:
            raw = json.load(f)

        self.features = []
        self.labels = []

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
                float(trade.get("rr_ratio", 2.0)) / 10.0,  # normalize RR
                float(trade.get("htf_4h_trend", 0)),
            ]
            label = 1.0 if result == "win" else 0.0

            self.features.append(feat)
            self.labels.append(label)

        logger.info(f"Loaded {len(self.features)} trades ({sum(self.labels):.0f} wins, {len(self.labels) - sum(self.labels):.0f} losses)")

    def __len__(self):
        return len(self.features)

    def __getitem__(self, idx):
        return torch.tensor(self.features[idx], dtype=torch.float32), \
               torch.tensor(self.labels[idx], dtype=torch.float32)


# ═══════════════════════════════════════════════════════════════
# TRAINING
# ═══════════════════════════════════════════════════════════════

def train(data_path: str, epochs: int = 200, lr: float = 0.001, batch_size: int = 16,
          val_split: float = 0.2, output_path: str = None):
    """Train the PriceTransformer model."""

    if output_path is None:
        output_path = str(BOT_DIR / "transformer_weights.pt")

    # Load data
    dataset = TradeDataset(data_path)
    if len(dataset) < 20:
        logger.error(f"Not enough data: {len(dataset)} trades (need at least 20)")
        return

    # Split train/val
    val_size = max(1, int(len(dataset) * val_split))
    train_size = len(dataset) - val_size
    train_ds, val_ds = random_split(dataset, [train_size, val_size],
                                     generator=torch.Generator().manual_seed(42))

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size)

    logger.info(f"Train: {train_size}, Val: {val_size}")

    # Model
    model = PriceTransformer(feature_dim=PriceTransformer.FEATURE_DIM)
    total_params = sum(p.numel() for p in model.parameters())
    logger.info(f"Model parameters: {total_params:,}")

    # Class imbalance: weight positive class higher (wins are rarer)
    n_wins = sum(1 for _, l in train_ds if l.item() > 0.5)
    n_losses = train_size - n_wins
    pos_weight = torch.tensor([n_losses / max(n_wins, 1)])
    logger.info(f"Class balance: {n_wins} wins / {n_losses} losses (pos_weight={pos_weight.item():.2f})")

    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    # Since we use Sigmoid in model, remove it for training with BCEWithLogitsLoss
    # Actually, let's use BCELoss since model already has Sigmoid
    criterion = nn.BCELoss()

    best_val_acc = 0.0
    best_epoch = 0
    patience = 30
    no_improve = 0

    for epoch in range(1, epochs + 1):
        # Train
        model.train()
        train_loss = 0.0
        train_correct = 0
        train_total = 0

        for features, labels in train_loader:
            optimizer.zero_grad()
            preds = model(features)
            loss = criterion(preds, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            train_loss += loss.item() * len(labels)
            train_correct += ((preds > 0.5).float() == labels).sum().item()
            train_total += len(labels)

        scheduler.step()

        # Validate
        model.eval()
        val_loss = 0.0
        val_correct = 0
        val_total = 0
        val_preds_all = []
        val_labels_all = []

        with torch.no_grad():
            for features, labels in val_loader:
                preds = model(features)
                loss = criterion(preds, labels)
                val_loss += loss.item() * len(labels)
                val_correct += ((preds > 0.5).float() == labels).sum().item()
                val_total += len(labels)
                val_preds_all.extend(preds.tolist())
                val_labels_all.extend(labels.tolist())

        train_acc = train_correct / max(train_total, 1) * 100
        val_acc = val_correct / max(val_total, 1) * 100
        avg_train_loss = train_loss / max(train_total, 1)
        avg_val_loss = val_loss / max(val_total, 1)

        if epoch % 20 == 0 or epoch == 1:
            logger.info(f"Epoch {epoch:3d}/{epochs} | "
                        f"Train: loss={avg_train_loss:.4f} acc={train_acc:.1f}% | "
                        f"Val: loss={avg_val_loss:.4f} acc={val_acc:.1f}%")

        # Save best model
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_epoch = epoch
            no_improve = 0
            torch.save({
                'model_state_dict': model.state_dict(),
                'epoch': epoch,
                'val_acc': val_acc,
                'train_acc': train_acc,
                'feature_dim': PriceTransformer.FEATURE_DIM,
            }, output_path)
        else:
            no_improve += 1

        # Early stopping
        if no_improve >= patience:
            logger.info(f"Early stopping at epoch {epoch} (no improvement for {patience} epochs)")
            break

    logger.info(f"Best model: epoch {best_epoch}, val_acc={best_val_acc:.1f}%")
    logger.info(f"Weights saved to {output_path}")

    # Final evaluation
    checkpoint = torch.load(output_path, weights_only=True)
    model.load_state_dict(checkpoint['model_state_dict'])
    model.eval()

    # Compute confusion matrix on val set
    tp = fp = tn = fn = 0
    with torch.no_grad():
        for features, labels in val_loader:
            preds = (model(features) > 0.5).float()
            for p, l in zip(preds, labels):
                if p == 1 and l == 1: tp += 1
                elif p == 1 and l == 0: fp += 1
                elif p == 0 and l == 0: tn += 1
                else: fn += 1

    precision = tp / max(tp + fp, 1) * 100
    recall = tp / max(tp + fn, 1) * 100

    logger.info(f"Confusion Matrix: TP={tp} FP={fp} TN={tn} FN={fn}")
    logger.info(f"Precision: {precision:.1f}% | Recall: {recall:.1f}%")

    return model


def main():
    parser = argparse.ArgumentParser(description="Train PriceTransformer")
    parser.add_argument("--data", type=str, default=str(BOT_DIR / "training_data.json"))
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--output", type=str, default=str(BOT_DIR / "transformer_weights.pt"))
    args = parser.parse_args()

    if not os.path.exists(args.data):
        logger.error(f"Training data not found: {args.data}")
        logger.info("Run backtester first: python backtester.py --all-whitelist --days 180 --threshold 0.35")
        return

    train(args.data, epochs=args.epochs, lr=args.lr,
          batch_size=args.batch_size, output_path=args.output)


if __name__ == "__main__":
    main()
