"""
База данных для хранения подписок.
Будет реализована в Этапе 2-3 (после подключения ЮKassa).

Используем SQLite — простая файловая БД, не требует установки.
"""

from typing import Optional, List

import aiosqlite
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = "subscriptions.db"


async def init_db() -> None:
    """Создаёт таблицы при первом запуске."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                tariff TEXT NOT NULL,
                price INTEGER NOT NULL,
                payment_id TEXT,
                started_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                name TEXT,
                phone TEXT,
                direction TEXT,
                goal TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                message_text TEXT,
                warning_number INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS content_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
        logger.info("База данных инициализирована")


async def save_lead(source: str, name: str, phone: str,
                    direction: str = "", goal: str = "") -> None:
    """Сохраняет заявку с сайта в БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO leads (source, name, phone, direction, goal) "
            "VALUES (?, ?, ?, ?, ?)",
            (source, name, phone, direction, goal),
        )
        await db.commit()


async def save_subscription(user_id: int, username: str, full_name: str,
                            tariff: str, price: int, payment_id: str) -> int:
    """Сохраняет подписку после оплаты. Возвращает ID подписки."""
    now = datetime.utcnow()
    expires = now + timedelta(days=30)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO subscriptions "
            "(user_id, username, full_name, tariff, price, payment_id, "
            "started_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, full_name, tariff, price, payment_id,
             now.isoformat(), expires.isoformat()),
        )
        await db.commit()
        return cursor.lastrowid


async def get_active_subscription(user_id: int) -> Optional[dict]:
    """Получает активную подписку пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM subscriptions "
            "WHERE user_id = ? AND is_active = 1 "
            "ORDER BY expires_at DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_expiring_subscriptions(days: int = 3) -> List[dict]:
    """Получает подписки, истекающие через N дней."""
    target_date = (datetime.utcnow() + timedelta(days=days)).date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM subscriptions "
            "WHERE is_active = 1 AND DATE(expires_at) = ?",
            (target_date,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_expired_subscriptions() -> List[dict]:
    """Получает просроченные подписки (для кика из канала)."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM subscriptions "
            "WHERE is_active = 1 AND expires_at < ?",
            (now,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def deactivate_subscription(subscription_id: int) -> None:
    """Деактивирует подписку (после кика из канала)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET is_active = 0 WHERE id = ?",
            (subscription_id,),
        )
        await db.commit()


# ── Предупреждения (модерация) ───────────────────────────────────

async def get_warning_count(user_id: int, chat_id: int) -> int:
    """Возвращает количество предупреждений пользователя в чате."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM warnings WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def add_warning(user_id: int, chat_id: int, username: str,
                      full_name: str, message_text: str,
                      warning_number: int, action: str) -> int:
    """Сохраняет предупреждение. Возвращает номер предупреждения."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO warnings "
            "(user_id, chat_id, username, full_name, message_text, "
            "warning_number, action) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, username, full_name,
             message_text[:200], warning_number, action),
        )
        await db.commit()
        return warning_number


async def save_content_title(content_type: str, title: str) -> None:
    """Сохраняет заголовок одобренного поста в историю."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO content_history (content_type, title) VALUES (?, ?)",
            (content_type, title),
        )
        await db.commit()


async def get_content_titles(content_type: str, limit: int = 20) -> list:
    """Возвращает последние N заголовков по типу контента."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT title FROM content_history "
            "WHERE content_type = ? ORDER BY created_at DESC LIMIT ?",
            (content_type, limit),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def reset_warnings(user_id: int, chat_id: int) -> int:
    """Сбрасывает все предупреждения пользователя в чате. Возвращает количество удалённых."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM warnings WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        await db.commit()
        return cursor.rowcount
