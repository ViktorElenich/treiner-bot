"""
Админ-команды для тренера: /stats, /users, /extend
Доступны только админу (ADMIN_CHAT_ID).
"""

import logging

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import load_config
from bot.database import (
    get_subscription_stats,
    get_all_active_subscriptions,
    get_active_subscription,
    extend_subscription,
)

router = Router()
logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом."""
    config = load_config()
    return user_id == config.admin_chat_id


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика подписок."""
    if not _is_admin(message.from_user.id):
        return

    stats = await get_subscription_stats()

    tariff_lines = []
    for tariff_id, count in stats["by_tariff"].items():
        tariff_lines.append(f"  {tariff_id}: {count}")
    tariff_text = "\n".join(tariff_lines) if tariff_lines else "  нет"

    text = (
        "📊 <b>Статистика подписок</b>\n\n"
        f"<b>Активных:</b> {stats['active_count']}\n"
        f"<b>Активная выручка:</b> {stats['active_revenue']:,} ₽\n\n"
        f"<b>Всего за всё время:</b> {stats['total_count']}\n"
        f"<b>Общая выручка:</b> {stats['total_revenue']:,} ₽\n\n"
        f"<b>По тарифам (активные):</b>\n{tariff_text}"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("users"))
async def cmd_users(message: Message):
    """Список активных подписчиков."""
    if not _is_admin(message.from_user.id):
        return

    subs = await get_all_active_subscriptions()

    if not subs:
        await message.answer("📋 Активных подписчиков нет.")
        return

    lines = []
    for sub in subs[:50]:  # Лимит 50 для длинных списков
        name = sub.get("full_name") or sub.get("username") or str(sub["user_id"])
        username = f" (@{sub['username']})" if sub.get("username") else ""
        expires = sub["expires_at"][:10]
        lines.append(
            f"• {name}{username} — {sub['tariff']} до {expires}"
        )

    text = (
        f"📋 <b>Активные подписчики ({len(subs)})</b>\n\n"
        + "\n".join(lines)
    )

    if len(subs) > 50:
        text += f"\n\n... и ещё {len(subs) - 50}"

    await message.answer(text, parse_mode="HTML")


@router.message(Command("extend"))
async def cmd_extend(message: Message, bot: Bot):
    """Продление подписки: /extend user_id days"""
    if not _is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer(
            "Использование: <code>/extend user_id days</code>\n"
            "Пример: <code>/extend 123456789 30</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_user_id = int(args[1])
        days = int(args[2])
    except ValueError:
        await message.answer("user_id и days должны быть числами.")
        return

    if days <= 0 or days > 365:
        await message.answer("Количество дней: от 1 до 365.")
        return

    sub = await get_active_subscription(target_user_id)
    if not sub:
        await message.answer(f"У пользователя {target_user_id} нет активной подписки.")
        return

    await extend_subscription(sub["id"], days)

    user_name = sub.get("full_name") or sub.get("username") or str(target_user_id)
    await message.answer(
        f"✅ Подписка продлена на {days} дн.\n\n"
        f"Клиент: {user_name}\n"
        f"Тариф: {sub['tariff']}",
        parse_mode="HTML",
    )

    # Уведомляем пользователя
    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=(
                f"🎉 <b>Подписка продлена!</b>\n\n"
                f"Тренер продлил твою подписку на {days} дн."
            ),
        )
    except Exception as e:
        logger.error("Не удалось уведомить user=%s о продлении: %s", target_user_id, e)
        await message.answer("(Не удалось отправить уведомление пользователю)")
