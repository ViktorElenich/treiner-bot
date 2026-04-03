"""
Генерация контента для Telegram-группы.

Поток:
1. Тренер пишет /content → выбирает тип
2. Бот генерирует текст + картинку через Gemini API
3. Присылает тренеру на проверку с кнопками 🔄 / ❌
4. Тренер одобряет → бот присылает чистый текст + картинку для копирования
5. Тренер сам публикует в нужный топик от своего имени
"""

import io
import logging

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
    BufferedInputFile,
)
from aiogram.filters import Command

from bot.config import load_config
from bot.database import get_content_titles, save_content_title
from bot.services.content_gen import generate_content, generate_image

router = Router()
logger = logging.getLogger(__name__)

# message_id → (content_type, title) — временное хранилище до одобрения тренером
_pending: dict = {}


# ── Клавиатура ───────────────────────────────────────────────────

def approval_keyboard(content_type: str) -> InlineKeyboardMarkup:
    """Кнопки для проверки контента."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Готово, дай текст",
                callback_data=f"content_approve_{content_type}",
            ),
            InlineKeyboardButton(
                text="🔄 Переделать",
                callback_data=f"content_regen_{content_type}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🖼 + Картинку",
                callback_data=f"content_image_{content_type}",
            ),
            InlineKeyboardButton(
                text="❌ Отменить",
                callback_data="content_cancel",
            ),
        ],
    ])


# ── Команда /content ─────────────────────────────────────────────

@router.message(Command("content"))
async def cmd_content(message: Message):
    """Меню генерации контента."""
    config = load_config()
    if message.from_user.id != config.admin_chat_id:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🥗 Питание",
                callback_data="content_gen_nutrition",
            ),
            InlineKeyboardButton(
                text="📚 Статья о спорте",
                callback_data="content_gen_article",
            ),
        ],
    ])

    await message.answer(
        "📝 <b>Генерация контента</b>\n\n"
        "Выбери тип поста. Я сгенерирую текст, "
        "ты проверишь и скопируешь в нужный топик.\n\n"
        "Также можно сгенерировать картинку к посту.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.message(Command("content_nutrition"))
async def cmd_content_nutrition(message: Message):
    """Сгенерировать пост о питании."""
    config = load_config()
    if message.from_user.id != config.admin_chat_id:
        return
    await _generate_and_show(message, "nutrition", config)


@router.message(Command("content_article"))
async def cmd_content_article(message: Message):
    """Сгенерировать статью о спорте."""
    config = load_config()
    if message.from_user.id != config.admin_chat_id:
        return
    await _generate_and_show(message, "article", config)


async def _generate_and_show(message: Message, content_type: str, config):
    """Генерирует текст и показывает превью."""
    label = "питании" if content_type == "nutrition" else "спорте"
    emoji = "🥗" if content_type == "nutrition" else "📚"

    await message.answer(f"⏳ Генерирую пост о {label}...")
    past_titles = await get_content_titles(content_type)
    title, text = await generate_content(content_type, config.gemini_api_key, past_titles)

    sent = await message.answer(
        f"{emoji} <b>Превью:</b>\n\n{text}",
        reply_markup=approval_keyboard(content_type),
        parse_mode="HTML",
    )
    _pending[sent.message_id] = (content_type, title)


# ── Callback: генерация из меню ──────────────────────────────────

@router.callback_query(F.data == "content_gen_nutrition")
async def cb_gen_nutrition(callback: CallbackQuery):
    config = load_config()
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Только для тренера", show_alert=True)
        return
    await callback.message.edit_text("⏳ Генерирую пост о питании...")
    await callback.answer()
    past_titles = await get_content_titles("nutrition")
    title, text = await generate_content("nutrition", config.gemini_api_key, past_titles)
    await callback.message.edit_text(
        f"🥗 <b>Превью:</b>\n\n{text}",
        reply_markup=approval_keyboard("nutrition"),
        parse_mode="HTML",
    )
    _pending[callback.message.message_id] = ("nutrition", title)


@router.callback_query(F.data == "content_gen_article")
async def cb_gen_article(callback: CallbackQuery):
    config = load_config()
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Только для тренера", show_alert=True)
        return
    await callback.message.edit_text("⏳ Генерирую статью о спорте...")
    await callback.answer()
    past_titles = await get_content_titles("article")
    title, text = await generate_content("article", config.gemini_api_key, past_titles)
    await callback.message.edit_text(
        f"📚 <b>Превью:</b>\n\n{text}",
        reply_markup=approval_keyboard("article"),
        parse_mode="HTML",
    )
    _pending[callback.message.message_id] = ("article", title)


# ── Callback: одобрение → чистый текст для копирования ───────────

@router.callback_query(F.data.startswith("content_approve_"))
async def cb_approve(callback: CallbackQuery, bot: Bot):
    """Тренер одобрил — присылаем чистый текст для копирования."""
    content_type = callback.data.replace("content_approve_", "")

    # Сохраняем тему в историю (если есть в pending)
    pending = _pending.pop(callback.message.message_id, None)
    if pending:
        saved_type, title = pending
        if title:
            await save_content_title(saved_type, title)
            logger.info("Тема сохранена в историю: type=%s, title=%r", saved_type, title)

    # Извлекаем текст (убираем заголовок "Превью:")
    full_text = callback.message.text or ""
    lines = full_text.split("\n", 2)
    post_text = lines[2] if len(lines) > 2 else full_text

    # Убираем превью-сообщение
    await callback.message.edit_text(
        "✅ <b>Текст готов — скопируй и вставь в нужный топик:</b>",
        parse_mode="HTML",
    )

    # Присылаем чистый текст отдельным сообщением (удобно копировать)
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=post_text,
    )

    await callback.answer()
    logger.info("Контент одобрен: type=%s", content_type)


# ── Callback: сгенерировать картинку ─────────────────────────────

@router.callback_query(F.data.startswith("content_image_"))
async def cb_generate_image(callback: CallbackQuery, bot: Bot):
    """Генерация картинки к посту через Kie AI (Nano Banana 2)."""
    config = load_config()
    content_type = callback.data.replace("content_image_", "")

    # Извлекаем текст для темы картинки
    full_text = callback.message.text or ""
    lines = full_text.split("\n", 2)
    post_text = lines[2] if len(lines) > 2 else full_text
    topic = post_text[:100]

    await callback.answer("⏳ Генерирую картинку (10-30 сек)...")

    image_data = await generate_image(topic, config.kie_api_key)

    if image_data:
        photo = BufferedInputFile(image_data, filename="post_image.jpg")
        await bot.send_photo(
            chat_id=callback.from_user.id,
            photo=photo,
            caption="🖼 Картинка к посту. Сохрани и отправь вместе с текстом.",
        )
    else:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="⚠️ Не удалось сгенерировать картинку. Попробуй ещё раз.",
        )


# ── Callback: переделать ─────────────────────────────────────────

@router.callback_query(F.data.startswith("content_regen_"))
async def cb_regen(callback: CallbackQuery):
    """Переделать контент."""
    config = load_config()
    content_type = callback.data.replace("content_regen_", "")
    emoji = "🥗" if content_type == "nutrition" else "📚"
    label = "питании" if content_type == "nutrition" else "спорте"

    _pending.pop(callback.message.message_id, None)
    await callback.message.edit_text(f"⏳ Генерирую новый пост о {label}...")
    await callback.answer()

    past_titles = await get_content_titles(content_type)
    title, text = await generate_content(content_type, config.gemini_api_key, past_titles)

    await callback.message.edit_text(
        f"{emoji} <b>Превью:</b>\n\n{text}",
        reply_markup=approval_keyboard(content_type),
        parse_mode="HTML",
    )
    _pending[callback.message.message_id] = (content_type, title)


# ── Callback: отменить ───────────────────────────────────────────

@router.callback_query(F.data == "content_cancel")
async def cb_cancel(callback: CallbackQuery):
    """Отменить."""
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()
