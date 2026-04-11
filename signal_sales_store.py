#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def to_iso(dt: datetime | None = None) -> str:
    value = dt or utc_now()
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


@dataclass(slots=True)
class SignalPayload:
    symbol: str
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    exchange: str = "BYBIT"
    signal_grade: str = "C"
    confidence: float = 0.0
    rr_ratio: float = 0.0
    leverage: str = ""
    order_type: str = "LIMIT"
    chart_url: str = ""
    source: str = "main_bot"
    created_at: str | None = None


class SignalSalesStore:
    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._ensure_signal_columns()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        ddl = """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS subscribers (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 0,
            subscription_started_at TEXT,
            subscription_until TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            tx_hash TEXT NOT NULL UNIQUE,
            amount_usdt REAL NOT NULL DEFAULT 0,
            asset TEXT NOT NULL DEFAULT 'USDT',
            network TEXT NOT NULL DEFAULT 'TRC20',
            wallet_address TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            note TEXT NOT NULL DEFAULT '',
            submitted_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewer_chat_id INTEGER,
            FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id)
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit REAL NOT NULL,
            exchange TEXT NOT NULL DEFAULT 'BYBIT',
            signal_grade TEXT NOT NULL DEFAULT 'C',
            confidence REAL NOT NULL DEFAULT 0,
            rr_ratio REAL NOT NULL DEFAULT 0,
            leverage TEXT NOT NULL DEFAULT '',
            order_type TEXT NOT NULL DEFAULT 'LIMIT',
            chart_url TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'main_bot',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            error TEXT NOT NULL DEFAULT '',
            delivered_at TEXT NOT NULL,
            UNIQUE(signal_id, chat_id),
            FOREIGN KEY(signal_id) REFERENCES signals(id),
            FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id)
        );

        CREATE TABLE IF NOT EXISTS signal_results (
            signal_id INTEGER PRIMARY KEY,
            outcome TEXT NOT NULL,
            max_pnl_pct REAL,
            max_drawdown_pct REAL,
            note TEXT NOT NULL DEFAULT '',
            closed_at TEXT NOT NULL,
            FOREIGN KEY(signal_id) REFERENCES signals(id)
        );

        CREATE TABLE IF NOT EXISTS weekly_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            week_end TEXT NOT NULL,
            total_signals INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            breakevens INTEGER NOT NULL DEFAULT 0,
            win_rate_pct REAL NOT NULL DEFAULT 0,
            max_profit_pct REAL NOT NULL DEFAULT 0,
            max_loss_pct REAL NOT NULL DEFAULT 0,
            avg_return_pct REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(week_start, week_end)
        );
        """
        with self._connect() as conn:
            conn.executescript(ddl)

    def _ensure_signal_columns(self) -> None:
        required_columns = {
            "leverage": "TEXT NOT NULL DEFAULT ''",
            "order_type": "TEXT NOT NULL DEFAULT 'LIMIT'",
            "chart_url": "TEXT NOT NULL DEFAULT ''",
        }
        with self._connect() as conn:
            rows = conn.execute("PRAGMA table_info(signals)").fetchall()
            existing = {str(row["name"]).lower() for row in rows}
            for column, ddl in required_columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE signals ADD COLUMN {column} {ddl}")

    def upsert_subscriber(self, chat_id: int, username: str | None = None, first_name: str | None = None) -> None:
        now = to_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subscribers (chat_id, username, first_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    updated_at=excluded.updated_at
                """,
                (chat_id, username or "", first_name or "", now, now),
            )

    def get_subscriber(self, chat_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone()
        return dict(row) if row else None

    def is_subscription_active(self, chat_id: int, now: datetime | None = None) -> bool:
        row = self.get_subscriber(chat_id)
        if not row or not int(row.get("is_active", 0)):
            return False
        until = parse_iso(row.get("subscription_until"))
        if until is None:
            return False
        return until > (now or utc_now())

    def create_payment_request(
        self,
        chat_id: int,
        tx_hash: str,
        amount_usdt: float,
        asset: str,
        network: str,
        wallet_address: str,
        note: str = "",
    ) -> int | None:
        tx = (tx_hash or "").strip().lower()
        if not tx:
            return None
        self.upsert_subscriber(chat_id)
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO payments (
                        chat_id, tx_hash, amount_usdt, asset, network, wallet_address, note, submitted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        tx,
                        max(float(amount_usdt), 0.0),
                        (asset or "USDT").upper(),
                        (network or "TRC20").upper(),
                        wallet_address,
                        note.strip(),
                        to_iso(),
                    ),
                )
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def list_pending_payments(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, s.username, s.first_name
                FROM payments p
                LEFT JOIN subscribers s ON s.chat_id = p.chat_id
                WHERE p.status = 'pending'
                ORDER BY p.id ASC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_payment(self, payment_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM payments WHERE id = ?", (int(payment_id),)).fetchone()
        return dict(row) if row else None

    def approve_payment(
        self,
        payment_id: int,
        reviewer_chat_id: int,
        duration_days: int,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        duration_days = max(int(duration_days), 1)
        current_time = now or utc_now()
        current_iso = to_iso(current_time)
        with self._connect() as conn:
            payment = conn.execute(
                "SELECT * FROM payments WHERE id = ? AND status = 'pending'",
                (payment_id,),
            ).fetchone()
            if not payment:
                return False, "pending payment not found"

            chat_id = int(payment["chat_id"])
            sub = conn.execute(
                "SELECT * FROM subscribers WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()

            current_until = parse_iso(sub["subscription_until"]) if sub else None
            start_time = current_until if current_until and current_until > current_time else current_time
            new_until = start_time + timedelta(days=duration_days)

            if sub:
                subscription_started = sub["subscription_started_at"] or current_iso
                if current_until is None or current_until <= current_time:
                    subscription_started = current_iso
                conn.execute(
                    """
                    UPDATE subscribers
                    SET is_active = 1,
                        subscription_started_at = ?,
                        subscription_until = ?,
                        updated_at = ?
                    WHERE chat_id = ?
                    """,
                    (subscription_started, to_iso(new_until), current_iso, chat_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO subscribers (
                        chat_id, username, first_name, is_active, subscription_started_at,
                        subscription_until, created_at, updated_at
                    ) VALUES (?, '', '', 1, ?, ?, ?, ?)
                    """,
                    (chat_id, current_iso, to_iso(new_until), current_iso, current_iso),
                )

            conn.execute(
                """
                UPDATE payments
                SET status = 'approved',
                    reviewed_at = ?,
                    reviewer_chat_id = ?
                WHERE id = ?
                """,
                (current_iso, reviewer_chat_id, payment_id),
            )
        return True, f"approved until {to_iso(new_until)}"

    def reject_payment(self, payment_id: int, reviewer_chat_id: int, note: str = "") -> tuple[bool, str]:
        with self._connect() as conn:
            payment = conn.execute(
                "SELECT * FROM payments WHERE id = ? AND status = 'pending'",
                (payment_id,),
            ).fetchone()
            if not payment:
                return False, "pending payment not found"

            conn.execute(
                """
                UPDATE payments
                SET status = 'rejected',
                    reviewed_at = ?,
                    reviewer_chat_id = ?,
                    note = CASE
                        WHEN note = '' THEN ?
                        ELSE note || ' | ' || ?
                    END
                WHERE id = ?
                """,
                (to_iso(), reviewer_chat_id, note.strip(), note.strip(), payment_id),
            )
        return True, "payment rejected"

    def insert_signal(self, payload: SignalPayload) -> int:
        created_at = payload.created_at or to_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO signals (
                    symbol, side, entry_price, stop_loss, take_profit, exchange,
                    signal_grade, confidence, rr_ratio, leverage, order_type, chart_url, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.symbol.upper(),
                    payload.side.upper(),
                    float(payload.entry_price),
                    float(payload.stop_loss),
                    float(payload.take_profit),
                    (payload.exchange or "BYBIT").upper(),
                    (payload.signal_grade or "C").upper(),
                    float(payload.confidence),
                    float(payload.rr_ratio),
                    str(payload.leverage or ""),
                    str(payload.order_type or "LIMIT").upper(),
                    str(payload.chart_url or "")[:1000],
                    payload.source,
                    created_at,
                ),
            )
        return int(cur.lastrowid)

    def fetch_signal(self, signal_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        return dict(row) if row else None

    def list_recent_signals(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM signals
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_pending_deliveries(self, limit: int = 200) -> list[dict[str, Any]]:
        now_iso = to_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.id AS signal_id,
                    s.symbol, s.side, s.entry_price, s.stop_loss, s.take_profit,
                    s.exchange, s.signal_grade, s.confidence, s.rr_ratio,
                    s.leverage, s.order_type, s.chart_url,
                    s.source, s.created_at,
                    sub.chat_id
                FROM signals s
                JOIN subscribers sub
                    ON sub.is_active = 1
                   AND sub.subscription_until IS NOT NULL
                   AND sub.subscription_until > ?
                   AND (
                        sub.subscription_started_at IS NULL
                        OR s.created_at >= sub.subscription_started_at
                   )
                LEFT JOIN deliveries d
                    ON d.signal_id = s.id AND d.chat_id = sub.chat_id
                WHERE d.id IS NULL
                ORDER BY s.id ASC, sub.chat_id ASC
                LIMIT ?
                """,
                (now_iso, max(int(limit), 1)),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_delivery(self, signal_id: int, chat_id: int, status: str = "sent", error: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deliveries (signal_id, chat_id, status, error, delivered_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(signal_id, chat_id) DO UPDATE SET
                    status = excluded.status,
                    error = excluded.error,
                    delivered_at = excluded.delivered_at
                """,
                (signal_id, chat_id, status, error[:600], to_iso()),
            )

    def set_signal_result(
        self,
        signal_id: int,
        outcome: str,
        max_pnl_pct: float | None = None,
        max_drawdown_pct: float | None = None,
        note: str = "",
        closed_at: datetime | None = None,
    ) -> None:
        normalized_outcome = (outcome or "").strip().lower()
        if normalized_outcome not in {"win", "loss", "breakeven"}:
            raise ValueError("outcome must be one of: win, loss, breakeven")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO signal_results (
                    signal_id, outcome, max_pnl_pct, max_drawdown_pct, note, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    outcome = excluded.outcome,
                    max_pnl_pct = excluded.max_pnl_pct,
                    max_drawdown_pct = excluded.max_drawdown_pct,
                    note = excluded.note,
                    closed_at = excluded.closed_at
                """,
                (
                    signal_id,
                    normalized_outcome,
                    None if max_pnl_pct is None else float(max_pnl_pct),
                    None if max_drawdown_pct is None else float(max_drawdown_pct),
                    note.strip(),
                    to_iso(closed_at),
                ),
            )

    def compute_weekly_summary(self, week_start: datetime, week_end: datetime) -> dict[str, Any]:
        start_iso = to_iso(week_start)
        end_iso = to_iso(week_end)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_signals,
                    SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) AS losses,
                    SUM(CASE WHEN outcome = 'breakeven' THEN 1 ELSE 0 END) AS breakevens,
                    MAX(COALESCE(max_pnl_pct, 0)) AS max_profit_pct,
                    MIN(COALESCE(max_drawdown_pct, max_pnl_pct, 0)) AS max_loss_pct,
                    AVG(COALESCE(max_pnl_pct, 0)) AS avg_return_pct
                FROM signal_results
                WHERE closed_at >= ? AND closed_at < ?
                """,
                (start_iso, end_iso),
            ).fetchone()

        total = int(row["total_signals"] or 0)
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        breakevens = int(row["breakevens"] or 0)
        win_rate = (wins / total * 100.0) if total > 0 else 0.0
        return {
            "week_start": start_iso,
            "week_end": end_iso,
            "total_signals": total,
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate_pct": round(win_rate, 2),
            "max_profit_pct": round(float(row["max_profit_pct"] or 0.0), 2),
            "max_loss_pct": round(float(row["max_loss_pct"] or 0.0), 2),
            "avg_return_pct": round(float(row["avg_return_pct"] or 0.0), 2),
        }

    def has_weekly_report(self, week_start_iso: str, week_end_iso: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM weekly_reports WHERE week_start = ? AND week_end = ?",
                (week_start_iso, week_end_iso),
            ).fetchone()
        return row is not None

    def save_weekly_report(self, summary: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO weekly_reports (
                    week_start, week_end, total_signals, wins, losses, breakevens,
                    win_rate_pct, max_profit_pct, max_loss_pct, avg_return_pct, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary["week_start"],
                    summary["week_end"],
                    int(summary["total_signals"]),
                    int(summary["wins"]),
                    int(summary["losses"]),
                    int(summary["breakevens"]),
                    float(summary["win_rate_pct"]),
                    float(summary["max_profit_pct"]),
                    float(summary["max_loss_pct"]),
                    float(summary["avg_return_pct"]),
                    to_iso(),
                ),
            )

    def get_active_subscriber_chat_ids(self, limit: int = 1000) -> list[int]:
        now_iso = to_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id
                FROM subscribers
                WHERE is_active = 1
                  AND subscription_until IS NOT NULL
                  AND subscription_until > ?
                ORDER BY chat_id ASC
                LIMIT ?
                """,
                (now_iso, max(int(limit), 1)),
            ).fetchall()
        return [int(row["chat_id"]) for row in rows]
