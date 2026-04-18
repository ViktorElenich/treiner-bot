"""
База данных (PostgreSQL через asyncpg).

Используем Neon/Postgres, потому что SQLite-файл на Render free tier
теряется при каждом редеплое (эфемерный диск).

Connection string берётся из env var DATABASE_URL.
Формат: postgresql://user:pass@host/db?sslmode=require
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

import asyncpg

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Возвращает connection pool, создаёт при первом вызове."""
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL не задан")
        # Neon требует SSL. asyncpg принимает sslmode в DSN,
        # но безопаснее передать ssl='require' явно.
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _row_to_dict(row: Optional[asyncpg.Record]) -> Optional[dict]:
    """Превращает Record в dict, даты — в isoformat (для совместимости)."""
    if row is None:
        return None
    d = dict(row)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _rows_to_dicts(rows: list) -> list[dict]:
    return [_row_to_dict(r) for r in rows]


async def init_db() -> None:
    """Создаёт таблицы при первом запуске."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT,
                full_name TEXT,
                tariff TEXT NOT NULL,
                price INTEGER NOT NULL,
                payment_id TEXT UNIQUE,
                started_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                source TEXT NOT NULL,
                name TEXT,
                phone TEXT,
                direction TEXT,
                goal TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chat_id BIGINT NOT NULL,
                username TEXT,
                full_name TEXT,
                message_text TEXT,
                warning_number INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS content_history (
                id SERIAL PRIMARY KEY,
                content_type TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS waitlist (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL UNIQUE,
                username TEXT,
                full_name TEXT,
                notified INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS protected_topics (
                chat_id BIGINT NOT NULL,
                thread_id BIGINT NOT NULL,
                title TEXT,
                created_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (chat_id, thread_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_chat_user "
            "ON ai_chat_history(user_id, created_at)"
        )
        logger.info("База данных инициализирована (PostgreSQL)")


# ── Заявки с сайта ───────────────────────────────────────────────

async def save_lead(source: str, name: str, phone: str,
                    direction: str = "", goal: str = "") -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO leads (source, name, phone, direction, goal) "
        "VALUES ($1, $2, $3, $4, $5)",
        source, name, phone, direction, goal,
    )


# ── Вейтлист ─────────────────────────────────────────────────────

async def add_to_waitlist(user_id: int, username: str = "",
                          full_name: str = "") -> bool:
    pool = await get_pool()
    result = await pool.execute(
        "INSERT INTO waitlist (user_id, username, full_name) "
        "VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING",
        user_id, username, full_name,
    )
    # result: "INSERT 0 <count>"
    try:
        inserted = int(result.split()[-1])
    except (ValueError, IndexError):
        inserted = 0
    return inserted > 0


async def get_waitlist_users() -> list:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT user_id, username, full_name FROM waitlist WHERE notified = 0"
    )
    return _rows_to_dicts(rows)


async def mark_waitlist_notified() -> int:
    pool = await get_pool()
    result = await pool.execute(
        "UPDATE waitlist SET notified = 1 WHERE notified = 0"
    )
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def clear_waitlist() -> None:
    pool = await get_pool()
    await pool.execute("DELETE FROM waitlist WHERE notified = 1")


# ── Подписки ─────────────────────────────────────────────────────

async def save_subscription(user_id: int, username: str, full_name: str,
                            tariff: str, price: int,
                            payment_id: str) -> Optional[int]:
    """
    Сохраняет подписку. Идемпотентно: если payment_id уже в БД — вернёт None.
    """
    now = datetime.utcnow()
    expires = now + timedelta(days=30)
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO subscriptions "
        "(user_id, username, full_name, tariff, price, payment_id, "
        "started_at, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (payment_id) DO NOTHING "
        "RETURNING id",
        user_id, username, full_name, tariff, price, payment_id, now, expires,
    )
    return row["id"] if row else None


async def get_active_subscription(user_id: int) -> Optional[dict]:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM subscriptions "
        "WHERE user_id = $1 AND is_active = 1 "
        "ORDER BY expires_at DESC LIMIT 1",
        user_id,
    )
    return _row_to_dict(row)


async def get_expiring_subscriptions(days: int = 3) -> list[dict]:
    """Подписки, истекающие через N дней (по дате UTC)."""
    target_date = (datetime.utcnow() + timedelta(days=days)).date()
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM subscriptions "
        "WHERE is_active = 1 AND (expires_at AT TIME ZONE 'UTC')::date = $1",
        target_date,
    )
    return _rows_to_dicts(rows)


async def get_expired_subscriptions() -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM subscriptions "
        "WHERE is_active = 1 AND expires_at < now()"
    )
    return _rows_to_dicts(rows)


async def deactivate_subscription(subscription_id: int) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE subscriptions SET is_active = 0 WHERE id = $1",
        subscription_id,
    )


async def get_all_active_subscriptions() -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM subscriptions WHERE is_active = 1 ORDER BY expires_at ASC"
    )
    return _rows_to_dicts(rows)


async def get_subscription_stats() -> dict:
    pool = await get_pool()
    async with pool.acquire() as db:
        active = await db.fetchrow(
            "SELECT COUNT(*)::int as cnt, COALESCE(SUM(price), 0)::int as revenue "
            "FROM subscriptions WHERE is_active = 1"
        )
        total = await db.fetchrow(
            "SELECT COUNT(*)::int as cnt, COALESCE(SUM(price), 0)::int as revenue "
            "FROM subscriptions"
        )
        by_tariff_rows = await db.fetch(
            "SELECT tariff, COUNT(*)::int as cnt "
            "FROM subscriptions WHERE is_active = 1 GROUP BY tariff"
        )
        return {
            "active_count": active["cnt"],
            "active_revenue": active["revenue"],
            "total_count": total["cnt"],
            "total_revenue": total["revenue"],
            "by_tariff": {r["tariff"]: r["cnt"] for r in by_tariff_rows},
        }


async def extend_subscription(subscription_id: int, days: int) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE subscriptions "
        "SET expires_at = expires_at + make_interval(days => $1) "
        "WHERE id = $2",
        days, subscription_id,
    )


# ── Предупреждения (модерация) ───────────────────────────────────

async def get_warning_count(user_id: int, chat_id: int) -> int:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT COUNT(*)::int AS cnt FROM warnings "
        "WHERE user_id = $1 AND chat_id = $2",
        user_id, chat_id,
    )
    return row["cnt"] if row else 0


async def add_warning(user_id: int, chat_id: int, username: str,
                      full_name: str, message_text: str,
                      warning_number: int, action: str) -> int:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO warnings "
        "(user_id, chat_id, username, full_name, message_text, "
        "warning_number, action) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        user_id, chat_id, username, full_name,
        message_text[:200], warning_number, action,
    )
    return warning_number


async def reset_warnings(user_id: int, chat_id: int) -> int:
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM warnings WHERE user_id = $1 AND chat_id = $2",
        user_id, chat_id,
    )
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


# ── История контента ────────────────────────────────────────────

async def save_content_title(content_type: str, title: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO content_history (content_type, title) VALUES ($1, $2)",
        content_type, title,
    )


async def get_content_titles(content_type: str, limit: int = 20) -> list:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT title FROM content_history "
        "WHERE content_type = $1 ORDER BY created_at DESC LIMIT $2",
        content_type, limit,
    )
    return [r["title"] for r in rows]


# ── Защищённые темы ──────────────────────────────────────────────

async def add_protected_topic(chat_id: int, thread_id: int,
                              title: str = "") -> bool:
    pool = await get_pool()
    result = await pool.execute(
        "INSERT INTO protected_topics (chat_id, thread_id, title) "
        "VALUES ($1, $2, $3) ON CONFLICT (chat_id, thread_id) DO NOTHING",
        chat_id, thread_id, title,
    )
    try:
        return int(result.split()[-1]) > 0
    except (ValueError, IndexError):
        return False


async def remove_protected_topic(chat_id: int, thread_id: int) -> bool:
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM protected_topics WHERE chat_id = $1 AND thread_id = $2",
        chat_id, thread_id,
    )
    try:
        return int(result.split()[-1]) > 0
    except (ValueError, IndexError):
        return False


async def is_topic_protected(chat_id: int, thread_id: int) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT 1 FROM protected_topics WHERE chat_id = $1 AND thread_id = $2",
        chat_id, thread_id,
    )
    return row is not None


# ── AI-чат ───────────────────────────────────────────────────────

async def save_chat_message(user_id: int, role: str, text: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO ai_chat_history (user_id, role, text) VALUES ($1, $2, $3)",
        user_id, role, text[:4000],
    )


async def get_chat_history(user_id: int, limit: int = 10) -> list:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT role, text FROM ("
        "  SELECT role, text, created_at FROM ai_chat_history"
        "  WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2"
        ") sub ORDER BY created_at ASC",
        user_id, limit,
    )
    return [{"role": r["role"], "text": r["text"]} for r in rows]


async def count_today_messages(user_id: int) -> int:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT COUNT(*)::int AS cnt FROM ai_chat_history "
        "WHERE user_id = $1 AND role = 'user' "
        "AND (created_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date",
        user_id,
    )
    return row["cnt"] if row else 0


async def cleanup_old_chat_history(days: int = 30) -> int:
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM ai_chat_history "
        "WHERE created_at < now() - make_interval(days => $1)",
        days,
    )
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0
