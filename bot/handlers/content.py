"""
Подготовка дайджестов исследований для Telegram-группы.

Поток:
1. Тренер пишет /content → выбирает тип
2. Бот находит свежие исследования PubMed и объясняет их через Gemini
3. Присылает тренеру на проверку с кнопками 🔄 / ❌
4. Тренер одобряет → бот присылает чистый текст и ссылки для копирования
5. Тренер сам публикует в нужный топик от своего имени
"""

import logging

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.filters import Command

from bot.config import load_config
from bot.database import (
    get_content_titles,
    save_content_title,
    save_research_sources,
)
from bot.services.content_gen import generate_content

router = Router()
logger = logging.getLogger(__name__)

# message_id → (content_type, title, research_sources) — временное хранилище до одобрения
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
        [InlineKeyboardButton(text="❌ Отменить", callback_data="content_cancel")],
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
                text="🥗 Исследования о питании",
                callback_data="content_gen_nutrition",
            ),
            InlineKeyboardButton(
                text="💪 Исследования о тренировках",
                callback_data="content_gen_article",
            ),
        ],
    ])

    await message.answer(
        "🔬 <b>Дайджест исследований</b>\n\n"
        "Выбери направление. Я найду свежие научные публикации в PubMed, "
        "кратко и понятно объясню результаты и добавлю прямые ссылки на источники.\n\n"
        "Ты проверишь выпуск и скопируешь его в нужный топик.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.message(Command("content_nutrition"))
async def cmd_content_nutrition(message: Message):
    """Подготовить дайджест исследований о питании."""
    config = load_config()
    if message.from_user.id != config.admin_chat_id:
        return
    await _generate_and_show(message, "nutrition", config)


@router.message(Command("content_article"))
async def cmd_content_article(message: Message):
    """Подготовить дайджест исследований о тренировках."""
    config = load_config()
    if message.from_user.id != config.admin_chat_id:
        return
    await _generate_and_show(message, "article", config)


async def _generate_and_show(message: Message, content_type: str, config):
    """Подбирает исследования и показывает превью."""
    label = "питании" if content_type == "nutrition" else "тренировках"
    emoji = "🥗" if content_type == "nutrition" else "💪"

    await message.answer(f"⏳ Ищу свежие исследования о {label}...")
    past_titles = await get_content_titles(content_type)
    title, text, research_sources = await generate_content(
        content_type, config.gemini_api_key, past_titles,
    )

    sent = await message.answer(
        f"{emoji} Превью дайджеста:\n\n{text}",
        reply_markup=approval_keyboard(content_type),
        parse_mode=None,
    )
    _pending[sent.message_id] = (content_type, title, research_sources)


# ── Callback: генерация из меню ──────────────────────────────────

@router.callback_query(F.data == "content_gen_nutrition")
async def cb_gen_nutrition(callback: CallbackQuery):
    config = load_config()
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Только для тренера", show_alert=True)
        return
    await callback.message.edit_text("⏳ Ищу свежие исследования о питании...")
    await callback.answer()
    past_titles = await get_content_titles("nutrition")
    title, text, research_sources = await generate_content(
        "nutrition", config.gemini_api_key, past_titles,
    )
    await callback.message.edit_text(
        f"🥗 Превью дайджеста:\n\n{text}",
        reply_markup=approval_keyboard("nutrition"),
        parse_mode=None,
    )
    _pending[callback.message.message_id] = ("nutrition", title, research_sources)


@router.callback_query(F.data == "content_gen_article")
async def cb_gen_article(callback: CallbackQuery):
    config = load_config()
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Только для тренера", show_alert=True)
        return
    await callback.message.edit_text("⏳ Ищу свежие исследования о тренировках...")
    await callback.answer()
    past_titles = await get_content_titles("article")
    title, text, research_sources = await generate_content(
        "article", config.gemini_api_key, past_titles,
    )
    await callback.message.edit_text(
        f"💪 Превью дайджеста:\n\n{text}",
        reply_markup=approval_keyboard("article"),
        parse_mode=None,
    )
    _pending[callback.message.message_id] = ("article", title, research_sources)


# ── Callback: одобрение → чистый текст для копирования ───────────

@router.callback_query(F.data.startswith("content_approve_"))
async def cb_approve(callback: CallbackQuery, bot: Bot):
    """Тренер одобрил — присылаем чистый текст для копирования."""
    content_type = callback.data.replace("content_approve_", "")

    # Сохраняем тему в историю (если есть в pending)
    pending = _pending.pop(callback.message.message_id, None)
    if pending:
        saved_type, title, research_sources = pending
        if title:
            await save_content_title(saved_type, title)
            logger.info("Тема сохранена в историю: type=%s, title=%r", saved_type, title)
        if research_sources:
            await save_research_sources(
                saved_type,
                [(paper.pmid, paper.title) for paper in research_sources],
            )

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


# ── Callback: переделать ─────────────────────────────────────────

@router.callback_query(F.data.startswith("content_regen_"))
async def cb_regen(callback: CallbackQuery):
    """Переделать контент."""
    config = load_config()
    content_type = callback.data.replace("content_regen_", "")
    emoji = "🥗" if content_type == "nutrition" else "💪"
    label = "питании" if content_type == "nutrition" else "тренировках"

    previous = _pending.pop(callback.message.message_id, None)
    excluded_pmids = {
        paper.pmid for paper in previous[2]
    } if previous else set()
    await callback.message.edit_text(f"⏳ Ищу другой свежий материал о {label}...")
    await callback.answer()

    past_titles = await get_content_titles(content_type)
    title, text, research_sources = await generate_content(
        content_type,
        config.gemini_api_key,
        past_titles,
        excluded_pmids=excluded_pmids,
    )

    await callback.message.edit_text(
        f"{emoji} Превью дайджеста:\n\n{text}",
        reply_markup=approval_keyboard(content_type),
        parse_mode=None,
    )
    _pending[callback.message.message_id] = (content_type, title, research_sources)


# ── Callback: отменить ───────────────────────────────────────────

@router.callback_query(F.data == "content_cancel")
async def cb_cancel(callback: CallbackQuery):
    """Отменить."""
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()
