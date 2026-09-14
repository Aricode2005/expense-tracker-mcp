"""
Expense Tracker MCP Server
===========================
A production-grade Model Context Protocol server for personal finance tracking.

Tools:  12  |  Resources: 2  |  Prompts: 1
Currency: INR (₹)
Backend:  SQLite

Author: Aritra Dutta
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastmcp import FastMCP

from db import CURRENCY, PAYMENT_METHODS, get_connection, init_db

# ── Bootstrap ───────────────────────────────────────────────────────
CATEGORIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")

mcp = FastMCP(
    "ExpenseTracker",
    instructions=(
        "You are an intelligent expense-tracking assistant. "
        "Use the available tools to add, query, edit, delete, summarise, "
        "and analyse the user's expenses stored in a local SQLite database. "
        "All monetary amounts are in Indian Rupees (₹ INR). "
        "Always confirm destructive actions before executing them."
    ),
)

# Initialise the database on import
init_db()


# ── Helpers ─────────────────────────────────────────────────────────

def _load_categories() -> dict[str, list[str]]:
    """Load categories from the JSON file (re-read each time so edits are live)."""
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_date(date_str: str) -> str | None:
    """Return an error message if date_str is not YYYY-MM-DD, else None."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return f"Invalid date format '{date_str}'. Expected YYYY-MM-DD."
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return f"Invalid date '{date_str}'. Not a real calendar date."
    return None


def _validate_category(category: str, subcategory: str = "") -> str | None:
    """Return an error message if category/subcategory is invalid, else None."""
    cats = _load_categories()
    if category not in cats:
        return f"Unknown category '{category}'. Valid: {', '.join(sorted(cats.keys()))}"
    if subcategory and subcategory not in cats[category]:
        return (
            f"Unknown subcategory '{subcategory}' for category '{category}'. "
            f"Valid: {', '.join(cats[category])}"
        )
    return None


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TOOLS  (12)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def add_expense(
    date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = "",
    payment_method: str = "cash",
    is_recurring: bool = False,
    tags: str = "",
) -> dict:
    """
    Add a new expense to the tracker.

    Args:
        date: Date of expense in YYYY-MM-DD format (e.g. '2026-09-14').
        amount: Amount spent in INR. Must be greater than 0.
        category: Expense category (e.g. 'food', 'transport'). Must match categories.json.
        subcategory: Optional sub-category (e.g. 'groceries'). Must match categories.json.
        note: Optional free-text description of the expense.
        payment_method: One of: cash, upi, credit_card, debit_card, net_banking, wallet.
        is_recurring: Whether this is a recurring expense (e.g. subscription, rent).
        tags: Optional comma-separated tags for flexible filtering (e.g. 'work,client-x').

    Returns:
        The newly created expense record with its ID, or an error message.
    """
    # ── Validation ──────────────────────────────────────────────────
    if err := _validate_date(date):
        return {"status": "error", "message": err}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than 0."}
    if err := _validate_category(category, subcategory):
        return {"status": "error", "message": err}
    if payment_method not in PAYMENT_METHODS:
        return {
            "status": "error",
            "message": f"Invalid payment_method '{payment_method}'. "
                       f"Valid: {', '.join(sorted(PAYMENT_METHODS))}",
        }

    now = _now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO expenses(date, amount, category, subcategory, note,
                                 payment_method, is_recurring, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (date, amount, category, subcategory, note,
             payment_method, int(is_recurring), tags, now, now),
        )
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {"status": "ok", "currency": CURRENCY, "expense": _row_to_dict(row)}


@mcp.tool()
def list_expenses(
    start_date: str,
    end_date: str,
    category: str = "",
    subcategory: str = "",
    payment_method: str = "",
    min_amount: float = 0.0,
    max_amount: float = 0.0,
    tag: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    List expenses within an inclusive date range, with optional filters.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD), inclusive.
        category: Optional — filter by category.
        subcategory: Optional — filter by subcategory.
        payment_method: Optional — filter by payment method.
        min_amount: Optional — minimum amount (inclusive).
        max_amount: Optional — maximum amount (inclusive, 0 = no upper limit).
        tag: Optional — filter expenses that contain this tag.
        limit: Max rows to return (default 50).
        offset: Pagination offset (default 0).

    Returns:
        A list of matching expense records and pagination metadata.
    """
    if err := _validate_date(start_date):
        return {"status": "error", "message": err}
    if err := _validate_date(end_date):
        return {"status": "error", "message": err}

    query = "SELECT * FROM expenses WHERE date BETWEEN ? AND ?"
    params: list = [start_date, end_date]

    if category:
        query += " AND category = ?"
        params.append(category)
    if subcategory:
        query += " AND subcategory = ?"
        params.append(subcategory)
    if payment_method:
        query += " AND payment_method = ?"
        params.append(payment_method)
    if min_amount > 0:
        query += " AND amount >= ?"
        params.append(min_amount)
    if max_amount > 0:
        query += " AND amount <= ?"
        params.append(max_amount)
    if tag:
        query += " AND (',' || tags || ',') LIKE ?"
        params.append(f"%,{tag},%")

    # Count total matches for pagination
    count_query = query.replace("SELECT *", "SELECT COUNT(*)", 1)
    query += " ORDER BY date DESC, id DESC LIMIT ? OFFSET ?"
    params_count = list(params)
    params.extend([limit, offset])

    with get_connection() as conn:
        total = conn.execute(count_query, params_count).fetchone()[0]
        rows = conn.execute(query, params).fetchall()

    return {
        "status": "ok",
        "currency": CURRENCY,
        "total_matches": total,
        "returned": len(rows),
        "limit": limit,
        "offset": offset,
        "expenses": [_row_to_dict(r) for r in rows],
    }


@mcp.tool()
def get_expense(expense_id: int) -> dict:
    """
    Fetch a single expense by its ID.

    Args:
        expense_id: The unique ID of the expense.

    Returns:
        The expense record, or an error if not found.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not row:
        return {"status": "error", "message": f"Expense #{expense_id} not found."}
    return {"status": "ok", "currency": CURRENCY, "expense": _row_to_dict(row)}


@mcp.tool()
def edit_expense(
    expense_id: int,
    date: str = "",
    amount: float = 0.0,
    category: str = "",
    subcategory: str = "",
    note: Optional[str] = None,
    payment_method: str = "",
    is_recurring: Optional[bool] = None,
    tags: Optional[str] = None,
) -> dict:
    """
    Edit an existing expense. Only the fields you provide will be updated.

    Args:
        expense_id: ID of the expense to edit.
        date: New date (YYYY-MM-DD). Leave empty to keep current.
        amount: New amount in INR. Pass 0 to keep current.
        category: New category. Leave empty to keep current.
        subcategory: New subcategory. Leave empty to keep current.
        note: New note. Pass None to keep current.
        payment_method: New payment method. Leave empty to keep current.
        is_recurring: New recurring flag. Pass None to keep current.
        tags: New tags. Pass None to keep current.

    Returns:
        The updated expense record, or an error.
    """
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if not existing:
            return {"status": "error", "message": f"Expense #{expense_id} not found."}

        updates: dict = {}

        if date:
            if err := _validate_date(date):
                return {"status": "error", "message": err}
            updates["date"] = date

        if amount > 0:
            updates["amount"] = amount

        if category:
            sub = subcategory or existing["subcategory"]
            if err := _validate_category(category, sub):
                return {"status": "error", "message": err}
            updates["category"] = category
            if subcategory:
                updates["subcategory"] = subcategory
        elif subcategory:
            if err := _validate_category(existing["category"], subcategory):
                return {"status": "error", "message": err}
            updates["subcategory"] = subcategory

        if note is not None:
            updates["note"] = note

        if payment_method:
            if payment_method not in PAYMENT_METHODS:
                return {
                    "status": "error",
                    "message": f"Invalid payment_method '{payment_method}'. "
                               f"Valid: {', '.join(sorted(PAYMENT_METHODS))}",
                }
            updates["payment_method"] = payment_method

        if is_recurring is not None:
            updates["is_recurring"] = int(is_recurring)

        if tags is not None:
            updates["tags"] = tags

        if not updates:
            return {"status": "error", "message": "No fields provided to update."}

        updates["updated_at"] = _now_iso()

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [expense_id]
        conn.execute(f"UPDATE expenses SET {set_clause} WHERE id = ?", values)

        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    return {"status": "ok", "currency": CURRENCY, "expense": _row_to_dict(row)}


@mcp.tool()
def delete_expense(expense_id: int) -> dict:
    """
    Delete an expense by its ID. Returns the deleted record for confirmation.

    Args:
        expense_id: ID of the expense to delete.

    Returns:
        The deleted expense record, or an error if not found.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if not row:
            return {"status": "error", "message": f"Expense #{expense_id} not found."}
        deleted = _row_to_dict(row)
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    return {"status": "ok", "message": f"Expense #{expense_id} deleted.", "currency": CURRENCY, "deleted_expense": deleted}


@mcp.tool()
def search_expenses(query: str, limit: int = 20) -> dict:
    """
    Full-text search across expense notes and tags.

    Args:
        query: Search term to look for in the 'note' and 'tags' fields.
        limit: Max results (default 20).

    Returns:
        Matching expenses.
    """
    if not query.strip():
        return {"status": "error", "message": "Search query cannot be empty."}

    pattern = f"%{query}%"
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM expenses
            WHERE note LIKE ? OR tags LIKE ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (pattern, pattern, limit),
        ).fetchall()

    return {
        "status": "ok",
        "currency": CURRENCY,
        "query": query,
        "results": len(rows),
        "expenses": [_row_to_dict(r) for r in rows],
    }


@mcp.tool()
def summarize_expenses(
    start_date: str,
    end_date: str,
    category: str = "",
    group_by: str = "category",
) -> dict:
    """
    Aggregate spending over a date range. Group by category, subcategory, month, or day.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD), inclusive.
        category: Optional — restrict to a single category.
        group_by: Grouping dimension: 'category' (default), 'subcategory', 'month', 'day'.

    Returns:
        Aggregated totals with count and average per group.
    """
    if err := _validate_date(start_date):
        return {"status": "error", "message": err}
    if err := _validate_date(end_date):
        return {"status": "error", "message": err}

    group_col_map = {
        "category": "category",
        "subcategory": "subcategory",
        "month": "SUBSTR(date, 1, 7)",   # YYYY-MM
        "day": "date",
    }
    if group_by not in group_col_map:
        return {"status": "error", "message": f"Invalid group_by '{group_by}'. Valid: {', '.join(group_col_map.keys())}"}

    group_col = group_col_map[group_by]

    query = f"""
        SELECT {group_col} AS group_key,
               COUNT(*)      AS count,
               SUM(amount)   AS total,
               ROUND(AVG(amount), 2) AS avg_amount,
               MIN(amount)   AS min_amount,
               MAX(amount)   AS max_amount
        FROM expenses
        WHERE date BETWEEN ? AND ?
    """
    params: list = [start_date, end_date]

    if category:
        query += " AND category = ?"
        params.append(category)

    query += f" GROUP BY {group_col} ORDER BY total DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        grand = conn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount),0) AS total FROM expenses WHERE date BETWEEN ? AND ?"
            + (" AND category = ?" if category else ""),
            params,
        ).fetchone()

    return {
        "status": "ok",
        "currency": CURRENCY,
        "date_range": {"start": start_date, "end": end_date},
        "group_by": group_by,
        "grand_total": grand["total"],
        "grand_count": grand["cnt"],
        "groups": [_row_to_dict(r) for r in rows],
    }


@mcp.tool()
def get_monthly_trend(months: int = 6) -> dict:
    """
    Show month-over-month spending totals for the last N months.

    Args:
        months: Number of past months to include (default 6).

    Returns:
        A list of {month, total, count} sorted chronologically.
    """
    if months < 1 or months > 60:
        return {"status": "error", "message": "months must be between 1 and 60."}

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT SUBSTR(date, 1, 7) AS month,
                   COUNT(*)           AS count,
                   SUM(amount)        AS total
            FROM expenses
            WHERE date >= DATE('now', ? || ' months')
            GROUP BY SUBSTR(date, 1, 7)
            ORDER BY month ASC
            """,
            (f"-{months}",),
        ).fetchall()

    return {
        "status": "ok",
        "currency": CURRENCY,
        "months_requested": months,
        "trend": [_row_to_dict(r) for r in rows],
    }


@mcp.tool()
def set_budget(category: str, monthly_limit: float) -> dict:
    """
    Set or update the monthly budget for a category.

    Args:
        category: The expense category (must exist in categories.json).
        monthly_limit: Monthly spending limit in INR.

    Returns:
        The created or updated budget record.
    """
    if err := _validate_category(category):
        return {"status": "error", "message": err}
    if monthly_limit <= 0:
        return {"status": "error", "message": "monthly_limit must be greater than 0."}

    now = _now_iso()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO budgets(category, monthly_limit, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit
            """,
            (category, monthly_limit, now),
        )
        row = conn.execute("SELECT * FROM budgets WHERE category = ?", (category,)).fetchone()
    return {"status": "ok", "currency": CURRENCY, "budget": _row_to_dict(row)}


@mcp.tool()
def get_budget_status(month: str = "") -> dict:
    """
    Compare actual spending vs budget for a given month (YYYY-MM).
    If month is empty, defaults to the current month.

    Args:
        month: Month in YYYY-MM format (e.g. '2026-09'). Defaults to current month.

    Returns:
        Per-category budget vs actual spending, with over/under flags.
    """
    if not month:
        month = datetime.now().strftime("%Y-%m")
    if not re.match(r"^\d{4}-\d{2}$", month):
        return {"status": "error", "message": f"Invalid month format '{month}'. Expected YYYY-MM."}

    start = f"{month}-01"
    # last day → next month's first day minus 1 day, but simpler: use LIKE
    with get_connection() as conn:
        budgets_rows = conn.execute("SELECT * FROM budgets ORDER BY category").fetchall()
        if not budgets_rows:
            return {"status": "ok", "message": "No budgets configured. Use set_budget to create one."}

        results = []
        for b in budgets_rows:
            actual = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS spent FROM expenses WHERE category = ? AND date LIKE ?",
                (b["category"], f"{month}%"),
            ).fetchone()
            spent = actual["spent"]
            limit_ = b["monthly_limit"]
            results.append({
                "category": b["category"],
                "monthly_limit": limit_,
                "spent": spent,
                "remaining": round(limit_ - spent, 2),
                "utilization_pct": round((spent / limit_) * 100, 1) if limit_ else 0,
                "status": "OVER_BUDGET" if spent > limit_ else ("WARNING" if spent > limit_ * 0.8 else "OK"),
            })

    return {
        "status": "ok",
        "currency": CURRENCY,
        "month": month,
        "budgets": results,
    }


@mcp.tool()
def export_expenses(start_date: str, end_date: str, category: str = "") -> dict:
    """
    Export expenses in a date range as CSV text (can be pasted into a spreadsheet).

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD), inclusive.
        category: Optional — restrict to a single category.

    Returns:
        CSV-formatted string of expense data.
    """
    if err := _validate_date(start_date):
        return {"status": "error", "message": err}
    if err := _validate_date(end_date):
        return {"status": "error", "message": err}

    query = "SELECT * FROM expenses WHERE date BETWEEN ? AND ?"
    params: list = [start_date, end_date]
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY date ASC, id ASC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return {"status": "ok", "message": "No expenses found in the given range.", "csv": ""}

    columns = rows[0].keys()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow([r[c] for c in columns])

    return {
        "status": "ok",
        "currency": CURRENCY,
        "rows_exported": len(rows),
        "csv": buf.getvalue(),
    }


@mcp.tool()
def get_category_breakdown(category: str, start_date: str, end_date: str) -> dict:
    """
    Break down spending for a single category by its subcategories.

    Args:
        category: The category to break down (e.g. 'food').
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD), inclusive.

    Returns:
        Subcategory-level totals and counts within the given category.
    """
    if err := _validate_date(start_date):
        return {"status": "error", "message": err}
    if err := _validate_date(end_date):
        return {"status": "error", "message": err}
    if err := _validate_category(category):
        return {"status": "error", "message": err}

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT subcategory,
                   COUNT(*)    AS count,
                   SUM(amount) AS total,
                   ROUND(AVG(amount), 2) AS avg_amount
            FROM expenses
            WHERE category = ? AND date BETWEEN ? AND ?
            GROUP BY subcategory
            ORDER BY total DESC
            """,
            (category, start_date, end_date),
        ).fetchall()

        grand = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE category = ? AND date BETWEEN ? AND ?",
            (category, start_date, end_date),
        ).fetchone()

    return {
        "status": "ok",
        "currency": CURRENCY,
        "category": category,
        "date_range": {"start": start_date, "end": end_date},
        "category_total": grand["total"],
        "subcategories": [_row_to_dict(r) for r in rows],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RESOURCES  (2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("expense://categories", mime_type="application/json")
def get_categories() -> str:
    """All available expense categories and their subcategories."""
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return f.read()


@mcp.resource("expense://stats", mime_type="application/json")
def get_stats() -> str:
    """Quick dashboard: total expenses, this month's spending, top categories."""
    now = datetime.now()
    month_prefix = now.strftime("%Y-%m")

    with get_connection() as conn:
        total_all = conn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total FROM expenses"
        ).fetchone()

        total_month = conn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total FROM expenses WHERE date LIKE ?",
            (f"{month_prefix}%",),
        ).fetchone()

        top_cats = conn.execute(
            """
            SELECT category, SUM(amount) AS total
            FROM expenses
            GROUP BY category
            ORDER BY total DESC
            LIMIT 5
            """,
        ).fetchall()

    stats = {
        "currency": CURRENCY,
        "all_time": {"count": total_all["cnt"], "total_spent": total_all["total"]},
        "this_month": {"month": month_prefix, "count": total_month["cnt"], "total_spent": total_month["total"]},
        "top_categories": [_row_to_dict(r) for r in top_cats],
    }
    return json.dumps(stats, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROMPTS  (1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.prompt()
def monthly_report(month: str, year: str) -> str:
    """
    Generate a comprehensive monthly expense report.

    Args:
        month: Month number (e.g. '09').
        year: Year (e.g. '2026').
    """
    return f"""Please generate a detailed monthly expense report for {year}-{month} (INR ₹).

Follow these steps:
1. Call `summarize_expenses` with start_date='{year}-{month}-01' and end_date='{year}-{month}-31' to get category-level totals.
2. Call `get_budget_status` with month='{year}-{month}' to compare spending against budgets.
3. Call `get_monthly_trend` with months=6 to show how this month compares to previous months.

Then produce a formatted report with:
- **Summary**: Total spent, number of transactions, daily average.
- **Category Breakdown**: Table of each category with total, count, and % of overall spend.
- **Budget Status**: For each budgeted category, show limit vs actual, remaining, and a status indicator (✅ OK / ⚠️ Warning / 🚨 Over Budget).
- **Trend Analysis**: Month-over-month comparison. Is spending increasing or decreasing?
- **Top 3 Recommendations**: Actionable suggestions to optimize spending based on the data.

Format everything as a clean, readable report with emojis and tables.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Entry point
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    mcp.run()