"""
Автоматическая модерация групповых чатов.
Фильтрует мат и оскорбления, выдаёт предупреждения, мут и бан.

Система наказаний:
  1-е нарушение → предупреждение (сообщение удалено)
  2-е нарушение → мут на 1 час
  3-е нарушение → бан из группы
"""

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.types import Message, ChatPermissions
from aiogram.filters import Command

from bot.config import load_config
from bot.data.bad_words import contains_profanity
from bot.database import (
    add_protected_topic,
    add_warning,
    get_warning_count,
    is_topic_protected,
    remove_protected_topic,
    reset_warnings,
)

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


# ── Защита тем: только админ может постить новые сообщения ──────

async def _should_delete_topic_post(message: Message, bot: Bot) -> bool:
    """
    Фильтр: нужно ли удалить это сообщение как нарушение темы.
    True — в защищённой теме не-админ пытается написать новый пост
    (не ответ на другой пост).
    """
    # Только в отслеживаемых супергруппах-форумах
    if message.chat.type != "supergroup":
        return False
    if message.chat.id not in _get_monitored_ids():
        return False
    if not message.message_thread_id:
        return False
    if not message.from_user:
        return False

    # Админ и бот всегда могут постить
    config = load_config()
    if message.from_user.id == config.admin_chat_id:
        return False
    me = await bot.get_me()
    if message.from_user.id == me.id:
        return False

    # Это реальный ответ на другой пост (комментарий)?
    reply = message.reply_to_message
    if reply:
        is_topic_root = (
            reply.message_id == message.message_thread_id
            or getattr(reply, "forum_topic_created", None) is not None
        )
        if not is_topic_root:
            # Ответ на конкретный пост — это комментарий, пропускаем
            return False

    # Топ-левел пост в теме. Удаляем, только если тема защищена.
    return await is_topic_protected(message.chat.id, message.message_thread_id)


async def _delete_notice_later(bot: Bot, chat_id: int, message_id: int,
                               delay: int = 10) -> None:
    """Удаляет уведомление через delay секунд."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


@router.message(_should_delete_topic_post)
async def delete_topic_post(message: Message, bot: Bot):
    """Удаляет топ-левел посты не-админов в защищённых темах."""
    try:
        await message.delete()
    except Exception as e:
        logger.error("Не удалось удалить пост в защищённой теме: %s", e)
        return

    # Показываем временное уведомление (10 сек)
    try:
        notice = await bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=(
                "💬 В этой теме можно только <b>комментировать</b> посты тренера.\n"
                "Чтобы ответить — нажми на пост и выбери «Ответить»."
            ),
            parse_mode="HTML",
        )
        asyncio.create_task(
            _delete_notice_later(bot, message.chat.id, notice.message_id)
        )
    except Exception as e:
        logger.error("Не удалось отправить уведомление в тему: %s", e)

    logger.info(
        "Защита темы: удалён пост от user=%s в chat=%s thread=%s",
        message.from_user.id, message.chat.id, message.message_thread_id,
    )


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


# ── Команды управления защитой тем ───────────────────────────────

@router.message(Command("topic_id"))
async def cmd_topic_id(message: Message):
    """Показывает ID текущей темы (для отладки)."""
    config = load_config()
    if message.from_user.id != config.admin_chat_id:
        return

    if not message.message_thread_id:
        await message.answer("💡 Эта команда работает только внутри темы.")
        return

    await message.answer(
        f"🆔 chat_id: <code>{message.chat.id}</code>\n"
        f"thread_id: <code>{message.message_thread_id}</code>",
        parse_mode="HTML",
    )


@router.message(Command("protect_topic"))
async def cmd_protect_topic(message: Message):
    """
    Защищает текущую тему: в ней смогут писать только админ,
    остальные — только комментарии под его постами.
    Запускать внутри нужной темы.
    """
    config = load_config()
    if message.from_user.id != config.admin_chat_id:
        return

    if not message.message_thread_id:
        await message.answer(
            "💡 Эту команду надо отправить внутри темы, которую защищаем."
        )
        return

    if message.chat.id not in _get_monitored_ids():
        await message.answer(
            "⚠️ Эта группа не в списке отслеживаемых — защита не сработает."
        )
        return

    # Попытаемся вытащить название темы из reply (если есть)
    title = ""
    if message.reply_to_message and getattr(
        message.reply_to_message, "forum_topic_created", None
    ):
        title = message.reply_to_message.forum_topic_created.name or ""

    added = await add_protected_topic(
        chat_id=message.chat.id,
        thread_id=message.message_thread_id,
        title=title,
    )
    if added:
        await message.answer(
            "✅ Тема защищена. Теперь здесь могут постить только ты, "
            "остальные — только комментировать твои посты."
        )
    else:
        await message.answer("ℹ️ Эта тема уже была защищена.")


@router.message(Command("unprotect_topic"))
async def cmd_unprotect_topic(message: Message):
    """Снимает защиту с текущей темы. Запускать внутри нужной темы."""
    config = load_config()
    if message.from_user.id != config.admin_chat_id:
        return

    if not message.message_thread_id:
        await message.answer("💡 Эту команду надо отправить внутри темы.")
        return

    removed = await remove_protected_topic(
        chat_id=message.chat.id,
        thread_id=message.message_thread_id,
    )
    if removed:
        await message.answer("✅ Защита снята. В теме снова могут писать все.")
    else:
        await message.answer("ℹ️ Эта тема не была защищена.")
