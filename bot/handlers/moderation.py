"""
Автоматическая модерация групповых чатов.
Фильтрует мат и оскорбления, выдаёт предупреждения, мут и бан.

Система наказаний:
  1-е нарушение → предупреждение (сообщение удалено)
  2-е нарушение → мут на 1 час
  3-е нарушение → бан из группы
"""

import logging
from datetime import datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.types import Message, ChatPermissions
from aiogram.filters import Command

from bot.config import load_config
from bot.data.bad_words import contains_profanity
from bot.database import get_warning_count, add_warning, reset_warnings

router = Router()
logger = logging.getLogger(__name__)


# ── Фильтр: только сообщения в наших группах ────────────────────

def _get_monitored_ids() -> list:
    """Возвращает список ID групп, в которых работает модерация."""
    config = load_config()
    return [
        gid for gid in [
            config.group_general_id,
            config.group_start_id,
            config.group_progress_id,
            config.group_result_id,
        ] if gid != 0
    ]


async def is_monitored_group(message: Message) -> bool:
    """Фильтр: пропускает только сообщения из отслеживаемых групп."""
    if message.chat.type not in ("group", "supergroup"):
        return False
    return message.chat.id in _get_monitored_ids()


# ── Основной обработчик сообщений ────────────────────────────────

@router.message(F.text, is_monitored_group, ~F.text.startswith("/"))
async def check_message(message: Message, bot: Bot):
    """Проверяет каждое текстовое сообщение в группе на мат (кроме команд)."""
    config = load_config()
    user = message.from_user

    if not user:
        return

    # Пропускаем сообщения от бота и от тренера (админа)
    me = await bot.get_me()
    if user.id == me.id or user.id == config.admin_chat_id:
        return

    # Проверяем на мат
    if not contains_profanity(message.text):
        return

    # ── Мат обнаружен ──
    chat_id = message.chat.id
    user_id = user.id
    username = user.username or ""
    full_name = user.full_name or ""
    user_mention = f"@{username}" if username else full_name

    # Удаляем сообщение
    try:
        await message.delete()
    except Exception as e:
        logger.error("Не удалось удалить сообщение: %s", e)

    # Считаем предупреждения и определяем наказание
    count = await get_warning_count(user_id, chat_id)
    new_count = count + 1

    if new_count == 1:
        # 1-е нарушение → предупреждение
        action = "warning"
        group_text = (
            f"⚠️ {user_mention}, это предупреждение (1/3).\n"
            f"В нашем чате запрещены мат и оскорбления."
        )
    elif new_count == 2:
        # 2-е нарушение → мут на 1 час
        action = "mute"
        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=timedelta(hours=1),
            )
        except Exception as e:
            logger.error("Не удалось замутить пользователя: %s", e)
        group_text = (
            f"🔇 {user_mention} заблокирован на 1 час (2/3).\n"
            f"Следующее нарушение — бан."
        )
    else:
        # 3-е нарушение → бан
        action = "ban"
        try:
            await bot.ban_chat_member(
                chat_id=chat_id,
                user_id=user_id,
            )
        except Exception as e:
            logger.error("Не удалось забанить пользователя: %s", e)
        group_text = (
            f"🚫 {user_mention} заблокирован за повторные нарушения."
        )

    # Сохраняем в БД
    await add_warning(
        user_id=user_id,
        chat_id=chat_id,
        username=username,
        full_name=full_name,
        message_text=message.text,
        warning_number=new_count,
        action=action,
    )

    # Отправляем сообщение в группу
    try:
        await bot.send_message(chat_id=chat_id, text=group_text)
    except Exception as e:
        logger.error("Не удалось отправить сообщение в группу: %s", e)

    # Уведомляем тренера
    chat_title = message.chat.title or "Неизвестный чат"
    admin_text = (
        f"🛡 <b>Модерация</b>\n\n"
        f"<b>Кто:</b> {full_name}"
    )
    if username:
        admin_text += f" (@{username})"
    admin_text += (
        f"\n<b>Группа:</b> {chat_title}"
        f"\n<b>Сообщение:</b> <i>{message.text[:100]}</i>"
        f"\n<b>Действие:</b> {action} ({new_count}/3)"
    )

    try:
        await bot.send_message(
            chat_id=config.admin_chat_id,
            text=admin_text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Не удалось уведомить админа: %s", e)

    logger.info(
        "Модерация: user=%s, action=%s (%d/3), chat=%s",
        user_id, action, new_count, chat_id,
    )


# ── Админ-команды ────────────────────────────────────────────────

@router.message(Command("reset_warnings"))
async def cmd_reset_warnings(message: Message):
    """
    Сброс предупреждений пользователя.
    Использование: ответить на сообщение пользователя и написать /reset_warnings
    """
    config = load_config()

    # Только тренер может сбрасывать
    if message.from_user.id != config.admin_chat_id:
        return

    # Нужен ответ на сообщение пользователя
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.answer(
            "💡 Ответь на сообщение пользователя и напиши /reset_warnings"
        )
        return

    target = message.reply_to_message.from_user
    deleted = await reset_warnings(target.id, message.chat.id)

    name = target.full_name or f"@{target.username}" or str(target.id)
    await message.answer(
        f"✅ Предупреждения для {name} сброшены (удалено: {deleted})."
    )


@router.message(Command("warnings"))
async def cmd_warnings(message: Message):
    """
    Проверить количество предупреждений пользователя.
    Использование: ответить на сообщение пользователя и написать /warnings
    """
    config = load_config()

    if message.from_user.id != config.admin_chat_id:
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.answer(
            "💡 Ответь на сообщение пользователя и напиши /warnings"
        )
        return

    target = message.reply_to_message.from_user
    count = await get_warning_count(target.id, message.chat.id)

    name = target.full_name or f"@{target.username}" or str(target.id)
    await message.answer(f"📊 {name}: {count}/3 предупреждений.")
