"""
Автопубликация постов в общий чат.

Поток:
1. Планировщик каждый день в 8:30 МСК вызывает send_daily_draft
2. Чередование по дню: чётный день года — питание, нечётный — тренировки
3. Бот генерирует текст + картинку и присылает тренеру с кнопками
4. Тренер жмёт «Опубликовать» — пост уходит в тему общего чата
   (питание → тема «Питание», тренировки → тема «Статьи о спорте»)

Черновики живут в памяти: после редеплоя кнопки старого черновика
перестают работать — тогда тренер запускает /autopost вручную.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
    BufferedInputFile,
)
from aiogram.filters import Command

from bot.config import Config, load_config
from bot.database import get_content_titles, save_content_title
from bot.services.content_gen import generate_content, generate_image

router = Router()
logger = logging.getLogger(__name__)

# message_id (сообщение с кнопками у тренера) → черновик
_drafts: dict = {}

# Telegram: подпись к фото не длиннее 1024 символов
CAPTION_LIMIT = 1024


def _today_content_type() -> str:
    """Чётный день года — питание, нечётный — тренировки (МСК)."""
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    return "nutrition" if today.toordinal() % 2 == 0 else "article"


def _labels(content_type: str) -> tuple:
    if content_type == "nutrition":
        return "🥗", "питании"
    return "💪", "тренировках"


def draft_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать", callback_data="autopost_publish"),
            InlineKeyboardButton(text="🔄 Переделать", callback_data="autopost_regen"),
        ],
        [
            InlineKeyboardButton(text="⏭ Пропустить сегодня", callback_data="autopost_skip"),
        ],
    ])


async def send_daily_draft(bot: Bot, config: Config, content_type: str = None) -> None:
    """Генерирует пост + картинку и присылает тренеру на одобрение."""
    content_type = content_type or _today_content_type()
    emoji, label = _labels(content_type)

    past_titles = await get_content_titles(content_type)
    title, text = await generate_content(content_type, config.gemini_api_key, past_titles)

    if not title:
        # generate_content вернул ошибку в text
        await bot.send_message(
            chat_id=config.admin_chat_id,
            text=f"⚠️ Автопост: не получилось сгенерировать текст.\n{text}",
            parse_mode=None,
        )
        return

    # Картинка (может занять до 2 минут; при ошибке публикуем без неё)
    photo_file_id = None
    image_data = await generate_image(title, config.kie_api_key)
    if image_data:
        photo_msg = await bot.send_photo(
            chat_id=config.admin_chat_id,
            photo=BufferedInputFile(image_data, filename="post_image.jpg"),
            caption="🖼 Картинка к сегодняшнему посту",
        )
        photo_file_id = photo_msg.photo[-1].file_id
    else:
        await bot.send_message(
            chat_id=config.admin_chat_id,
            text="⚠️ Картинка не сгенерировалась — пост будет без неё.",
        )

    sent = await bot.send_message(
        chat_id=config.admin_chat_id,
        text=(
            f"{emoji} Пост на сегодня — о {label}.\n"
            f"Проверь и жми кнопку:\n\n{text}"
        ),
        reply_markup=draft_keyboard(),
        parse_mode=None,
    )
    _drafts[sent.message_id] = {
        "content_type": content_type,
        "title": title,
        "text": text,
        "photo_file_id": photo_file_id,
    }
    logger.info("Автопост-черновик отправлен тренеру: type=%s, title=%r", content_type, title)


# ── Команда /autopost — сгенерировать черновик вручную ──────────

@router.message(Command("autopost"))
async def cmd_autopost(message: Message, bot: Bot):
    """Ручной запуск: /autopost, /autopost nutrition, /autopost article."""
    config = load_config()
    if message.from_user.id != config.admin_chat_id:
        return

    parts = (message.text or "").split()
    content_type = parts[1] if len(parts) > 1 and parts[1] in ("nutrition", "article") else None

    await message.answer("⏳ Генерирую пост и картинку (до 2-3 минут)...")
    await send_daily_draft(bot, config, content_type)


# ── Callback: опубликовать ───────────────────────────────────────

@router.callback_query(F.data == "autopost_publish")
async def cb_publish(callback: CallbackQuery, bot: Bot):
    config = load_config()
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Только для тренера", show_alert=True)
        return

    draft = _drafts.pop(callback.message.message_id, None)
    if not draft:
        await callback.answer(
            "Черновик устарел (бот перезапускался). Запусти /autopost заново.",
            show_alert=True,
        )
        return

    if draft["content_type"] == "nutrition":
        thread_id = config.topic_nutrition_id
    else:
        thread_id = config.topic_articles_id
    thread_id = thread_id or None  # 0 → без темы, в общий поток

    chat_id = config.group_general_id
    text = draft["text"]

    await callback.answer("Публикую...")
    try:
        if draft["photo_file_id"] and len(text) <= CAPTION_LIMIT:
            await bot.send_photo(
                chat_id=chat_id,
                photo=draft["photo_file_id"],
                caption=text,
                message_thread_id=thread_id,
                parse_mode=None,
            )
        else:
            if draft["photo_file_id"]:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=draft["photo_file_id"],
                    message_thread_id=thread_id,
                )
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                message_thread_id=thread_id,
                parse_mode=None,
            )
    except Exception as e:
        logger.error("Не удалось опубликовать автопост: %s", e, exc_info=True)
        _drafts[callback.message.message_id] = draft  # вернуть, чтобы можно было повторить
        await bot.send_message(
            chat_id=config.admin_chat_id,
            text=f"⚠️ Не получилось опубликовать: {e}\nПопробуй нажать «Опубликовать» ещё раз.",
            parse_mode=None,
        )
        return

    if draft["title"]:
        await save_content_title(draft["content_type"], draft["title"])

    emoji, label = _labels(draft["content_type"])
    await callback.message.edit_text(
        f"✅ Опубликовано в тему о {label}:\n\n{text}",
        parse_mode=None,
    )
    logger.info("Автопост опубликован: type=%s, title=%r", draft["content_type"], draft["title"])


# ── Callback: переделать ─────────────────────────────────────────

@router.callback_query(F.data == "autopost_regen")
async def cb_regen(callback: CallbackQuery, bot: Bot):
    config = load_config()
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Только для тренера", show_alert=True)
        return

    draft = _drafts.pop(callback.message.message_id, None)
    if not draft:
        await callback.answer(
            "Черновик устарел (бот перезапускался). Запусти /autopost заново.",
            show_alert=True,
        )
        return

    await callback.answer("Переделываю (до 2-3 минут)...")
    await callback.message.edit_text("⏳ Генерирую новый вариант...")
    await send_daily_draft(bot, config, draft["content_type"])


# ── Callback: пропустить ─────────────────────────────────────────

@router.callback_query(F.data == "autopost_skip")
async def cb_skip(callback: CallbackQuery):
    config = load_config()
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Только для тренера", show_alert=True)
        return

    _drafts.pop(callback.message.message_id, None)
    await callback.message.edit_text("⏭ Пропущено — сегодня без автопоста.")
    await callback.answer()
