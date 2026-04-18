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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS waitlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                notified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS protected_topics (
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                title TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, thread_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_chat_user "
            "ON ai_chat_history(user_id, created_at)"
        )
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


async def add_to_waitlist(user_id: int, username: str = "",
                          full_name: str = "") -> bool:
    """
    Добавляет пользователя в лист ожидания.
    Возвращает True если добавлен, False если уже был в списке.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO waitlist (user_id, username, full_name) "
                "VALUES (?, ?, ?)",
                (user_id, username, full_name),
            )
            await db.commit()
            return True
        except Exception:
            # UNIQUE constraint — уже в списке
            return False


async def get_waitlist_users() -> list:
    """Возвращает список пользователей в листе ожидания (не уведомлённых)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_id, username, full_name FROM waitlist "
            "WHERE notified = 0"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def mark_waitlist_notified() -> int:
    """
    Помечает всех в вейтлисте как уведомлённых.
    Возвращает количество помеченных.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE waitlist SET notified = 1 WHERE notified = 0"
        )
        await db.commit()
        return cursor.rowcount


async def clear_waitlist() -> None:
    """Очищает весь лист ожидания (после уведомления)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM waitlist WHERE notified = 1")
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


async def get_all_active_subscriptions() -> List[dict]:
    """Возвращает все активные подписки (для /users)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM subscriptions "
            "WHERE is_active = 1 ORDER BY expires_at ASC",
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_subscription_stats() -> dict:
    """Статистика подписок: количество активных, выручка, разбивка по тарифам."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Общее количество активных и выручка
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(price), 0) as revenue "
            "FROM subscriptions WHERE is_active = 1",
        )
        row = await cursor.fetchone()
        active_count = row[0]
        active_revenue = row[1]

        # Всего подписок за всё время
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(price), 0) as revenue "
            "FROM subscriptions",
        )
        row = await cursor.fetchone()
        total_count = row[0]
        total_revenue = row[1]

        # Разбивка по тарифам (активные)
        cursor = await db.execute(
            "SELECT tariff, COUNT(*) as cnt "
            "FROM subscriptions WHERE is_active = 1 GROUP BY tariff",
        )
        by_tariff = {r[0]: r[1] for r in await cursor.fetchall()}

        return {
            "active_count": active_count,
            "active_revenue": active_revenue,
            "total_count": total_count,
            "total_revenue": total_revenue,
            "by_tariff": by_tariff,
        }


async def extend_subscription(subscription_id: int, days: int) -> None:
    """Продлевает подписку на N дней."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions "
            "SET expires_at = datetime(expires_at, '+' || ? || ' days') "
            "WHERE id = ?",
            (days, subscription_id),
        )
        await db.commit()


# ── Защищённые темы (только админ может постить) ────────────────

async def add_protected_topic(chat_id: int, thread_id: int,
                              title: str = "") -> bool:
    """Помечает тему как защищённую. True — добавлена, False — уже была."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO protected_topics (chat_id, thread_id, title) "
                "VALUES (?, ?, ?)",
                (chat_id, thread_id, title),
            )
            await db.commit()
            return True
        except Exception:
            return False


async def remove_protected_topic(chat_id: int, thread_id: int) -> bool:
    """Снимает защиту с темы. True — была защищена, False — не была."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM protected_topics WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def is_topic_protected(chat_id: int, thread_id: int) -> bool:
    """Проверяет, защищена ли тема."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM protected_topics WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        )
        return await cursor.fetchone() is not None


async def reset_warnings(user_id: int, chat_id: int) -> int:
    """Сбрасывает все предупреждения пользователя в чате. Возвращает количество удалённых."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM warnings WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        await db.commit()
        return cursor.rowcount


# ── AI-чат (история диалогов) ────────────────────────────────────

async def save_chat_message(user_id: int, role: str, text: str) -> None:
    """Сохраняет сообщение AI-чата (role: 'user' или 'model')."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ai_chat_history (user_id, role, text) VALUES (?, ?, ?)",
            (user_id, role, text[:4000]),
        )
        await db.commit()


async def get_chat_history(user_id: int, limit: int = 10) -> list:
    """Возвращает последние N сообщений диалога в хронологическом порядке."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT role, text FROM ("
            "  SELECT role, text, created_at FROM ai_chat_history"
            "  WHERE user_id = ? ORDER BY created_at DESC LIMIT ?"
            ") ORDER BY created_at ASC",
            (user_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def count_today_messages(user_id: int) -> int:
    """Считает сообщения пользователя (role='user') за сегодня (UTC)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM ai_chat_history "
            "WHERE user_id = ? AND role = 'user' AND DATE(created_at) = DATE('now')",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def cleanup_old_chat_history(days: int = 30) -> int:
    """Удаляет историю AI-чата старше N дней. Возвращает количество удалённых записей."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM ai_chat_history "
            "WHERE created_at < datetime('now', ? || ' days')",
            (f"-{days}",),
        )
        await db.commit()
        return cursor.rowcount
