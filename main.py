"""
Expense Tracker MCP Server  (Async Edition)
=============================================
A production-grade Model Context Protocol server for personal finance tracking.

Tools:  15  |  Resources: 2  |  Prompts: 1
Currency: INR (₹)
Backend:  SQLite (aiosqlite — fully async, non-blocking I/O)

Author: Aritra Dutta
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Optional

from fastmcp import FastMCP

from db import CURRENCY, PAYMENT_METHODS, get_connection

# ── Bootstrap ───────────────────────────────────────────────────────

mcp = FastMCP(
    "ExpenseTracker",
    instructions=(
        "You are an intelligent expense-tracking assistant. "
        "Use the available tools to add, query, edit, delete, summarise, "
        "and analyse the user's expenses stored in a local SQLite database. "
        "All monetary amounts are in Indian Rupees (₹ INR). "
        "Categories are fully editable — you can add or remove them. "
        "Always confirm destructive actions before executing them."
    ),
)


# ── Helpers ─────────────────────────────────────────────────────────

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


async def _get_categories_dict() -> dict[str, list[str]]:
    """Load categories from the database as {category: [subcategories]}."""
    async with get_connection() as conn:
        rows = await conn.execute_fetchall(
            "SELECT category, subcategory FROM categories ORDER BY category, subcategory"
        )
    result: dict[str, list[str]] = {}
    for r in rows:
        cat = r["category"]
        result.setdefault(cat, []).append(r["subcategory"])
    return result


async def _validate_category(category: str, subcategory: str = "") -> str | None:
    """Return an error message if category/subcategory is invalid, else None."""
    cats = await _get_categories_dict()
    if category not in cats:
        return f"Unknown category '{category}'. Valid: {', '.join(sorted(cats.keys()))}"
    if subcategory and subcategory not in cats[category]:
        return (
            f"Unknown subcategory '{subcategory}' for category '{category}'. "
            f"Valid: {', '.join(cats[category])}"
        )
    return None


def _row_to_dict(row) -> dict:
    """Convert an aiosqlite.Row to a plain dict."""
    return dict(row)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TOOLS — Expense CRUD  (5)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
async def add_expense(
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
        category: Expense category (e.g. 'food', 'transport'). Must exist in categories.
        subcategory: Optional sub-category (e.g. 'groceries'). Must exist under the category.
        note: Optional free-text description of the expense.
        payment_method: One of: cash, upi, credit_card, debit_card, net_banking, wallet.
        is_recurring: Whether this is a recurring expense (e.g. subscription, rent).
        tags: Optional comma-separated tags for flexible filtering (e.g. 'work,client-x').

    Returns:
        The newly created expense record with its ID, or an error message.
    """
    if err := _validate_date(date):
        return {"status": "error", "message": err}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than 0."}
    if err := await _validate_category(category, subcategory):
        return {"status": "error", "message": err}
    if payment_method not in PAYMENT_METHODS:
        return {
            "status": "error",
            "message": f"Invalid payment_method '{payment_method}'. "
                       f"Valid: {', '.join(sorted(PAYMENT_METHODS))}",
        }

    now = _now_iso()
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            INSERT INTO expenses(date, amount, category, subcategory, note,
                                 payment_method, is_recurring, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (date, amount, category, subcategory, note,
             payment_method, int(is_recurring), tags, now, now),
        )
        await conn.commit()
        row = await conn.execute_fetchall(
            "SELECT * FROM expenses WHERE id = ?", (cursor.lastrowid,)
        )
    return {"status": "ok", "currency": CURRENCY, "expense": _row_to_dict(row[0])}


@mcp.tool()
async def list_expenses(
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

    count_query = query.replace("SELECT *", "SELECT COUNT(*)", 1)
    query += " ORDER BY date DESC, id DESC LIMIT ? OFFSET ?"
    params_count = list(params)
    params.extend([limit, offset])

    async with get_connection() as conn:
        total_row = await conn.execute_fetchall(count_query, params_count)
        total = total_row[0][0]
        rows = await conn.execute_fetchall(query, params)

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
async def get_expense(expense_id: int) -> dict:
    """
    Fetch a single expense by its ID.

    Args:
        expense_id: The unique ID of the expense.

    Returns:
        The expense record, or an error if not found.
    """
    async with get_connection() as conn:
        rows = await conn.execute_fetchall(
            "SELECT * FROM expenses WHERE id = ?", (expense_id,)
        )
    if not rows:
        return {"status": "error", "message": f"Expense #{expense_id} not found."}
    return {"status": "ok", "currency": CURRENCY, "expense": _row_to_dict(rows[0])}


@mcp.tool()
async def edit_expense(
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
    async with get_connection() as conn:
        existing_rows = await conn.execute_fetchall(
            "SELECT * FROM expenses WHERE id = ?", (expense_id,)
        )
        if not existing_rows:
            return {"status": "error", "message": f"Expense #{expense_id} not found."}

        existing = _row_to_dict(existing_rows[0])
        updates: dict = {}

        if date:
            if err := _validate_date(date):
                return {"status": "error", "message": err}
            updates["date"] = date

        if amount > 0:
            updates["amount"] = amount

        if category:
            sub = subcategory or existing["subcategory"]
            if err := await _validate_category(category, sub):
                return {"status": "error", "message": err}
            updates["category"] = category
            if subcategory:
                updates["subcategory"] = subcategory
        elif subcategory:
            if err := await _validate_category(existing["category"], subcategory):
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
        await conn.execute(f"UPDATE expenses SET {set_clause} WHERE id = ?", values)
        await conn.commit()

        updated_rows = await conn.execute_fetchall(
            "SELECT * FROM expenses WHERE id = ?", (expense_id,)
        )
    return {"status": "ok", "currency": CURRENCY, "expense": _row_to_dict(updated_rows[0])}


@mcp.tool()
async def delete_expense(expense_id: int) -> dict:
    """
    Delete an expense by its ID. Returns the deleted record for confirmation.

    Args:
        expense_id: ID of the expense to delete.

    Returns:
        The deleted expense record, or an error if not found.
    """
    async with get_connection() as conn:
        rows = await conn.execute_fetchall(
            "SELECT * FROM expenses WHERE id = ?", (expense_id,)
        )
        if not rows:
            return {"status": "error", "message": f"Expense #{expense_id} not found."}
        deleted = _row_to_dict(rows[0])
        await conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        await conn.commit()
    return {
        "status": "ok",
        "message": f"Expense #{expense_id} deleted.",
        "currency": CURRENCY,
        "deleted_expense": deleted,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TOOLS — Search & Analytics  (5)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
async def search_expenses(query: str, limit: int = 20) -> dict:
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
    async with get_connection() as conn:
        rows = await conn.execute_fetchall(
            """
            SELECT * FROM expenses
            WHERE note LIKE ? OR tags LIKE ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (pattern, pattern, limit),
        )

    return {
        "status": "ok",
        "currency": CURRENCY,
        "query": query,
        "results": len(rows),
        "expenses": [_row_to_dict(r) for r in rows],
    }


@mcp.tool()
async def summarize_expenses(
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
        "month": "SUBSTR(date, 1, 7)",
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

    grand_query = (
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount),0) AS total FROM expenses WHERE date BETWEEN ? AND ?"
        + (" AND category = ?" if category else "")
    )

    async with get_connection() as conn:
        rows = await conn.execute_fetchall(query, params)
        grand_rows = await conn.execute_fetchall(grand_query, params)

    return {
        "status": "ok",
        "currency": CURRENCY,
        "date_range": {"start": start_date, "end": end_date},
        "group_by": group_by,
        "grand_total": grand_rows[0]["total"],
        "grand_count": grand_rows[0]["cnt"],
        "groups": [_row_to_dict(r) for r in rows],
    }


@mcp.tool()
async def get_monthly_trend(months: int = 6) -> dict:
    """
    Show month-over-month spending totals for the last N months.

    Args:
        months: Number of past months to include (default 6).

    Returns:
        A list of {month, total, count} sorted chronologically.
    """
    if months < 1 or months > 60:
        return {"status": "error", "message": "months must be between 1 and 60."}

    async with get_connection() as conn:
        rows = await conn.execute_fetchall(
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
        )

    return {
        "status": "ok",
        "currency": CURRENCY,
        "months_requested": months,
        "trend": [_row_to_dict(r) for r in rows],
    }


@mcp.tool()
async def export_expenses(start_date: str, end_date: str, category: str = "") -> dict:
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

    async with get_connection() as conn:
        rows = await conn.execute_fetchall(query, params)

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
async def get_category_breakdown(category: str, start_date: str, end_date: str) -> dict:
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
    if err := await _validate_category(category):
        return {"status": "error", "message": err}

    async with get_connection() as conn:
        rows = await conn.execute_fetchall(
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
        )
        grand = await conn.execute_fetchall(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE category = ? AND date BETWEEN ? AND ?",
            (category, start_date, end_date),
        )

    return {
        "status": "ok",
        "currency": CURRENCY,
        "category": category,
        "date_range": {"start": start_date, "end": end_date},
        "category_total": grand[0]["total"],
        "subcategories": [_row_to_dict(r) for r in rows],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TOOLS — Budget  (2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
async def set_budget(category: str, monthly_limit: float) -> dict:
    """
    Set or update the monthly budget for a category.

    Args:
        category: The expense category (must exist in categories).
        monthly_limit: Monthly spending limit in INR.

    Returns:
        The created or updated budget record.
    """
    if err := await _validate_category(category):
        return {"status": "error", "message": err}
    if monthly_limit <= 0:
        return {"status": "error", "message": "monthly_limit must be greater than 0."}

    now = _now_iso()
    async with get_connection() as conn:
        await conn.execute(
            """
            INSERT INTO budgets(category, monthly_limit, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit
            """,
            (category, monthly_limit, now),
        )
        await conn.commit()
        rows = await conn.execute_fetchall(
            "SELECT * FROM budgets WHERE category = ?", (category,)
        )
    return {"status": "ok", "currency": CURRENCY, "budget": _row_to_dict(rows[0])}


@mcp.tool()
async def get_budget_status(month: str = "") -> dict:
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

    async with get_connection() as conn:
        budgets_rows = await conn.execute_fetchall(
            "SELECT * FROM budgets ORDER BY category"
        )
        if not budgets_rows:
            return {"status": "ok", "message": "No budgets configured. Use set_budget to create one."}

        results = []
        for b in budgets_rows:
            actual = await conn.execute_fetchall(
                "SELECT COALESCE(SUM(amount), 0) AS spent FROM expenses WHERE category = ? AND date LIKE ?",
                (b["category"], f"{month}%"),
            )
            spent = actual[0]["spent"]
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TOOLS — Category Management  (3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
async def list_categories() -> dict:
    """
    List all available expense categories and their subcategories.

    Returns:
        A dictionary of categories and their subcategories.
    """
    cats = await _get_categories_dict()
    return {
        "status": "ok",
        "total_categories": len(cats),
        "total_subcategories": sum(len(v) for v in cats.values()),
        "categories": cats,
    }


@mcp.tool()
async def add_category(category: str, subcategory: str) -> dict:
    """
    Add a new category or subcategory to the tracker.
    If the category exists, adds the subcategory under it.
    If neither exists, creates both.

    Args:
        category: Category name (e.g. 'insurance', 'side_hustle'). Use lowercase with underscores.
        subcategory: Subcategory name (e.g. 'life_insurance', 'freelancing').

    Returns:
        Confirmation with the updated category.
    """
    category = category.strip().lower().replace(" ", "_")
    subcategory = subcategory.strip().lower().replace(" ", "_")

    if not category or not subcategory:
        return {"status": "error", "message": "Both category and subcategory are required."}

    async with get_connection() as conn:
        # Check if already exists
        existing = await conn.execute_fetchall(
            "SELECT id FROM categories WHERE category = ? AND subcategory = ?",
            (category, subcategory),
        )
        if existing:
            return {
                "status": "error",
                "message": f"'{subcategory}' already exists under '{category}'.",
            }

        await conn.execute(
            "INSERT INTO categories(category, subcategory) VALUES (?, ?)",
            (category, subcategory),
        )
        await conn.commit()

        # Return updated list for this category
        rows = await conn.execute_fetchall(
            "SELECT subcategory FROM categories WHERE category = ? ORDER BY subcategory",
            (category,),
        )

    return {
        "status": "ok",
        "message": f"Added subcategory '{subcategory}' under '{category}'.",
        "category": category,
        "subcategories": [r["subcategory"] for r in rows],
    }


@mcp.tool()
async def remove_category(category: str, subcategory: str = "") -> dict:
    """
    Remove a subcategory, or an entire category (all its subcategories).

    Args:
        category: The category to remove from.
        subcategory: If provided, only this subcategory is removed.
                     If empty, the ENTIRE category and all its subcategories are removed.

    Returns:
        Confirmation with the count of removed entries.
    """
    category = category.strip().lower()
    subcategory = subcategory.strip().lower() if subcategory else ""

    async with get_connection() as conn:
        if subcategory:
            existing = await conn.execute_fetchall(
                "SELECT id FROM categories WHERE category = ? AND subcategory = ?",
                (category, subcategory),
            )
            if not existing:
                return {"status": "error", "message": f"'{subcategory}' not found under '{category}'."}
            await conn.execute(
                "DELETE FROM categories WHERE category = ? AND subcategory = ?",
                (category, subcategory),
            )
            await conn.commit()
            return {
                "status": "ok",
                "message": f"Removed subcategory '{subcategory}' from '{category}'.",
                "removed_count": 1,
            }
        else:
            existing = await conn.execute_fetchall(
                "SELECT id FROM categories WHERE category = ?", (category,)
            )
            if not existing:
                return {"status": "error", "message": f"Category '{category}' not found."}
            count = len(existing)
            await conn.execute(
                "DELETE FROM categories WHERE category = ?", (category,)
            )
            await conn.commit()
            return {
                "status": "ok",
                "message": f"Removed entire category '{category}' with {count} subcategories.",
                "removed_count": count,
            }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RESOURCES  (2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("expense://categories", mime_type="application/json")
async def get_categories_resource() -> str:
    """All available expense categories and their subcategories (from database)."""
    cats = await _get_categories_dict()
    return json.dumps(cats, indent=2)


@mcp.resource("expense://stats", mime_type="application/json")
async def get_stats() -> str:
    """Quick dashboard: total expenses, this month's spending, top categories."""
    now = datetime.now()
    month_prefix = now.strftime("%Y-%m")

    async with get_connection() as conn:
        total_all = await conn.execute_fetchall(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total FROM expenses"
        )
        total_month = await conn.execute_fetchall(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total FROM expenses WHERE date LIKE ?",
            (f"{month_prefix}%",),
        )
        top_cats = await conn.execute_fetchall(
            """
            SELECT category, SUM(amount) AS total
            FROM expenses
            GROUP BY category
            ORDER BY total DESC
            LIMIT 5
            """,
        )

    stats = {
        "currency": CURRENCY,
        "all_time": {"count": total_all[0]["cnt"], "total_spent": total_all[0]["total"]},
        "this_month": {"month": month_prefix, "count": total_month[0]["cnt"], "total_spent": total_month[0]["total"]},
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
    import sys

    # Usage:
    #   python main.py              → STDIO  (MCP Inspector / Claude Desktop)
    #   python main.py --remote     → HTTP   (Remote deployment, streamable-http)
    #   python main.py --sse        → SSE    (Legacy remote, Server-Sent Events)

    if "--remote" in sys.argv:
        import os
        port = int(os.getenv("PORT", "8000"))
        print(f"🚀 Starting Expense Tracker MCP Server on http://0.0.0.0:{port} (streamable-http)")
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    elif "--sse" in sys.argv:
        import os
        port = int(os.getenv("PORT", "8000"))
        print(f"🚀 Starting Expense Tracker MCP Server on http://0.0.0.0:{port} (SSE)")
        mcp.run(transport="sse", host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")
