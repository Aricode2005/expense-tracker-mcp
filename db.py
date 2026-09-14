"""
Database module for Expense Tracker MCP Server.

Owns the SQLite schema (expenses + budgets tables) and provides
connection helpers used by every tool in main.py.
"""

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expenses.db")

# ── Currency ────────────────────────────────────────────────────────
CURRENCY = "INR"

# ── Allowed payment methods ─────────────────────────────────────────
PAYMENT_METHODS = frozenset(
    {"cash", "upi", "credit_card", "debit_card", "net_banking", "wallet"}
)


def get_connection() -> sqlite3.Connection:
    """Return a connection with Row factory for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # better concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now_iso() -> str:
    """Current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """Create tables if they do not already exist."""
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT    NOT NULL,          -- YYYY-MM-DD
                amount          REAL    NOT NULL CHECK(amount > 0),
                category        TEXT    NOT NULL,
                subcategory     TEXT    NOT NULL DEFAULT '',
                note            TEXT    NOT NULL DEFAULT '',
                payment_method  TEXT    NOT NULL DEFAULT 'cash',
                is_recurring    INTEGER NOT NULL DEFAULT 0 CHECK(is_recurring IN (0, 1)),
                tags            TEXT    NOT NULL DEFAULT '',  -- comma-separated
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS budgets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category        TEXT    NOT NULL UNIQUE,
                monthly_limit   REAL    NOT NULL CHECK(monthly_limit > 0),
                created_at      TEXT    NOT NULL
            );

            -- Speed up date-range queries
            CREATE INDEX IF NOT EXISTS idx_expenses_date     ON expenses(date);
            CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category);
            """
        )
