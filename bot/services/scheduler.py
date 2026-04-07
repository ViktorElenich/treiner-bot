"""
Планировщик для проверки подписок.
Ежедневно проверяет: напоминания за 3 дня, за 1 день, просроченные — кик.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from aiogram import Bot
from bot.config import Config
from bot.database import (
    get_expiring_subscriptions,
    get_expired_subscriptions,
    deactivate_subscription,
    get_waitlist_users,
    mark_waitlist_notified,
    clear_waitlist,
)
from bot.services.channel import get_group_id_for_tariff
from bot.keyboards.inline import renew_subscription_keyboard, tariffs_keyboard

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


def start_scheduler(bot: Bot, config: Config) -> None:
    """Настраивает и запускает планировщик задач."""
    scheduler.add_job(
        check_expiring_3days, "cron", hour=10, minute=0,
        args=[bot, config], id="check_expiring_3days", replace_existing=True,
    )
    scheduler.add_job(
        check_expiring_1day, "cron", hour=10, minute=5,
        args=[bot, config], id="check_expiring_1day", replace_existing=True,
    )
    scheduler.add_job(
        check_expired, "cron", hour=10, minute=10,
        args=[bot, config], id="check_expired", replace_existing=True,
    )
    # 20-го числа каждого месяца — рассылка вейтлисту: набор открыт!
    scheduler.add_job(
        notify_waitlist, "cron", day=20, hour=10, minute=15,
        args=[bot, config], id="notify_waitlist", replace_existing=True,
    )
    scheduler.start()
    logger.info("Планировщик подписок запущен (ежедневно в 10:00 МСК)")


def stop_scheduler() -> None:
    """Останавливает планировщик."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Планировщик остановлен")


async def check_expiring_3days(bot: Bot, config: Config) -> None:
    """Напоминание за 3 дня до окончания подписки."""
    subs = await get_expiring_subscriptions(days=3)
    for sub in subs:
        try:
            await bot.send_message(
                chat_id=sub["user_id"],
                text=(
                    "⏳ <b>Подписка скоро закончится</b>\n\n"
                    f"Через 3 дня истекает твоя подписка.\n"
                    f"Продли сейчас, чтобы не потерять доступ!"
                ),
                reply_markup=renew_subscription_keyboard(sub["tariff"]),
            )
            logger.info("Напоминание (3 дня) отправлено user=%s", sub["user_id"])
        except Exception as e:
            logger.error("Не удалось отправить напоминание user=%s: %s", sub["user_id"], e)


async def check_expiring_1day(bot: Bot, config: Config) -> None:
    """Срочное напоминание за 1 день до окончания подписки."""
    subs = await get_expiring_subscriptions(days=1)
    for sub in subs:
        try:
            await bot.send_message(
                chat_id=sub["user_id"],
                text=(
                    "🔴 <b>Подписка истекает завтра!</b>\n\n"
                    f"Завтра ты потеряешь доступ к группам.\n"
                    f"Продли подписку прямо сейчас!"
                ),
                reply_markup=renew_subscription_keyboard(sub["tariff"]),
            )
            logger.info("Напоминание (1 день) отправлено user=%s", sub["user_id"])
        except Exception as e:
            logger.error("Не удалось отправить напоминание user=%s: %s", sub["user_id"], e)


async def check_expired(bot: Bot, config: Config) -> None:
    """Кик из групп + деактивация просроченных подписок."""
    subs = await get_expired_subscriptions()
    for sub in subs:
        user_id = sub["user_id"]
        tariff_id = sub["tariff"]

        # Кик из группы тарифа
        group_id = get_group_id_for_tariff(tariff_id, config)
        if group_id:
            await _kick_user(bot, group_id, user_id)

        # Кик из общей группы
        if config.group_general_id:
            await _kick_user(bot, config.group_general_id, user_id)

        # Деактивируем подписку
        await deactivate_subscription(sub["id"])

        # Уведомляем пользователя
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ <b>Подписка истекла</b>\n\n"
                    "Твоя подписка закончилась, доступ к группам закрыт.\n"
                    "Ты можешь продлить подписку в любой момент!"
                ),
                reply_markup=renew_subscription_keyboard(tariff_id),
            )
        except Exception as e:
            logger.error("Не удалось уведомить user=%s об истечении: %s", user_id, e)

        # Уведомляем тренера
        user_name = sub.get("full_name") or sub.get("username") or str(user_id)
        try:
            await bot.send_message(
                chat_id=config.admin_chat_id,
                text=(
                    f"📤 <b>Подписка истекла</b>\n\n"
                    f"Клиент: {user_name}\n"
                    f"Тариф: {tariff_id}\n"
                    f"Удалён из групп."
                ),
            )
        except Exception as e:
            logger.error("Не удалось уведомить админа об истечении: %s", e)

        logger.info("Подписка истекла: user=%s, tariff=%s — кикнут", user_id, tariff_id)


async def notify_waitlist(bot: Bot, config: Config) -> None:
    """
    20-го числа: рассылка всем из листа ожидания — набор открыт!
    После рассылки помечаем как уведомлённых и очищаем.
    """
    users = await get_waitlist_users()
    if not users:
        logger.info("Лист ожидания пуст — рассылка не нужна")
        return

    sent = 0
    for user in users:
        try:
            await bot.send_message(
                chat_id=user["user_id"],
                text=(
                    "🔥 <b>Набор на онлайн-программы открыт!</b>\n\n"
                    "Ты записывался в лист ожидания — теперь можно "
                    "выбрать тариф и начать тренироваться!\n\n"
                    "Набор открыт до 3-го числа. Выбери тариф 👇"
                ),
                reply_markup=tariffs_keyboard(),
                parse_mode="HTML",
            )
            sent += 1
        except Exception as e:
            logger.error(
                "Не удалось уведомить waitlist user=%s: %s",
                user["user_id"], e,
            )

    await mark_waitlist_notified()
    await clear_waitlist()

    # Уведомляем тренера
    try:
        await bot.send_message(
            chat_id=config.admin_chat_id,
            text=(
                f"📋 <b>Рассылка по листу ожидания</b>\n\n"
                f"Набор открыт! Уведомлено: {sent} из {len(users)} человек."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Не удалось уведомить админа о рассылке: %s", e)

    logger.info("Waitlist рассылка: %s/%s уведомлены", sent, len(users))


async def _kick_user(bot: Bot, chat_id: int, user_id: int) -> None:
    """Кикает пользователя из группы (ban + unban = кик без постоянного бана)."""
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        logger.info("Пользователь %s кикнут из %s", user_id, chat_id)
    except Exception as e:
        logger.error("Не удалось кикнуть %s из %s: %s", user_id, chat_id, e)
