"""
Async database module for Expense Tracker MCP Server.

Uses aiosqlite for non-blocking I/O. Owns the SQLite schema
(expenses, budgets, categories tables) and provides async helpers.
"""

import json
import os
import sqlite3

import aiosqlite

# ── Database path ───────────────────────────────────────────────────
# Local: store alongside main.py
# Cloud: use DB_DIR env var, or /tmp as fallback for read-only filesystems
_default_dir = os.path.dirname(os.path.abspath(__file__))
_db_dir = os.getenv("DB_DIR", _default_dir)

# Test if default dir is writable; if not, fall back to /tmp
if _db_dir == _default_dir:
    try:
        _test_file = os.path.join(_default_dir, ".write_test")
        with open(_test_file, "w") as f:
            f.write("test")
        os.remove(_test_file)
    except OSError:
        _db_dir = os.environ.get("TMPDIR", "/tmp")

DB_PATH = os.path.join(_db_dir, "expenses.db")
CATEGORIES_SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")

CURRENCY = "INR"

PAYMENT_METHODS = frozenset(
    {"cash", "upi", "credit_card", "debit_card", "net_banking", "wallet"}
)


from contextlib import asynccontextmanager

@asynccontextmanager
async def get_connection():
    """Return an async connection with Row factory for dict-like access."""
    conn = await aiosqlite.connect(DB_PATH)
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        await conn.close()


def _init_db_sync() -> None:
    """
    Synchronous DB init — called once at import time to create tables.
    Uses plain sqlite3 because aiosqlite requires a running event loop.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT    NOT NULL,
                amount          REAL    NOT NULL CHECK(amount > 0),
                category        TEXT    NOT NULL,
                subcategory     TEXT    NOT NULL DEFAULT '',
                note            TEXT    NOT NULL DEFAULT '',
                payment_method  TEXT    NOT NULL DEFAULT 'cash',
                is_recurring    INTEGER NOT NULL DEFAULT 0 CHECK(is_recurring IN (0, 1)),
                tags            TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS budgets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category        TEXT    NOT NULL UNIQUE,
                monthly_limit   REAL    NOT NULL CHECK(monthly_limit > 0),
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS categories (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category        TEXT    NOT NULL,
                subcategory     TEXT    NOT NULL,
                UNIQUE(category, subcategory)
            );

            CREATE INDEX IF NOT EXISTS idx_expenses_date     ON expenses(date);
            CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category);
            CREATE INDEX IF NOT EXISTS idx_categories_cat    ON categories(category);
            """
        )

        count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if count == 0 and os.path.exists(CATEGORIES_SEED_PATH):
            with open(CATEGORIES_SEED_PATH, "r", encoding="utf-8") as f:
                cats = json.load(f)
            rows = []
            for cat, subs in cats.items():
                for sub in subs:
                    rows.append((cat, sub))
            conn.executemany(
                "INSERT OR IGNORE INTO categories(category, subcategory) VALUES (?, ?)",
                rows,
            )
            conn.commit()


_init_db_sync()
