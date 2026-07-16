"""
Автопубликация постов в общий чат.

Поток:
1. Планировщик каждый день в 8:30 МСК вызывает send_daily_draft
2. Чередование по дню: чётный день года — питание, нечётный — тренировки
3. Бот генерирует текст + картинку и присылает тренеру с кнопками
4. Тренер жмёт «Опубликовать» — пост уходит в тему общего чата
   (питание → тема «Питание», тренировки → тема «Статьи о спорте»)

Дополнительно: кнопка «🎤 Надиктую сам» — тренер отправляет голосовое
(или текст), Gemini расшифровывает и оформляет его слова в пост,
дальше то же одобрение.

Черновики живут в памяти: после редеплоя кнопки старого черновика
перестают работать — тогда тренер запускает /autopost вручную.
"""

import asyncio
import io
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
from bot.services.content_gen import generate_content, generate_image, structure_dictation

router = Router()
logger = logging.getLogger(__name__)

# message_id (сообщение с кнопками у тренера) → черновик
_drafts: dict = {}

# user_id тренера → {"content_type", "topic"} — ждём надиктовку
_dictation: dict = {}

# Telegram: подпись к фото не длиннее 1024 символов
CAPTION_LIMIT = 1024

# Если утренняя генерация не удалась — повторяем через 30 минут (до 2 раз)
RETRY_DELAY_SEC = 30 * 60

# Держим ссылки на фоновые задачи повтора, чтобы их не съел сборщик мусора
_retry_tasks: set = set()


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
            InlineKeyboardButton(text="🎤 Надиктую сам", callback_data="autopost_dictate"),
            InlineKeyboardButton(text="⏭ Пропустить", callback_data="autopost_skip"),
        ],
    ])


async def send_daily_draft(
    bot: Bot,
    config: Config,
    content_type: str = None,
    retries_left: int = 2,
) -> None:
    """Генерирует пост + картинку и присылает тренеру на одобрение.

    retries_left — сколько ещё раз автоматически повторить через 30 минут,
    если генерация не удалась (Gemini иногда временно отклоняет запросы
    с серверов Render). Для ручного запуска /autopost повторы отключены.
    """
    content_type = content_type or _today_content_type()

    past_titles = await get_content_titles(content_type)
    title, text = await generate_content(content_type, config.gemini_api_key, past_titles)

    if not title:
        # generate_content вернул ошибку в text
        if retries_left > 0:
            await bot.send_message(
                chat_id=config.admin_chat_id,
                text=(
                    "⚠️ Автопост: Google временно не отвечает, "
                    "попробую ещё раз через 30 минут — ничего делать не нужно.\n"
                    f"{text}"
                ),
                parse_mode=None,
            )
            task = asyncio.create_task(
                _retry_later(bot, config, content_type, retries_left - 1)
            )
            _retry_tasks.add(task)
            task.add_done_callback(_retry_tasks.discard)
        else:
            await bot.send_message(
                chat_id=config.admin_chat_id,
                text=(
                    "⚠️ Автопост: не получилось сгенерировать текст.\n"
                    f"{text}\n\n"
                    "Можно попробовать вручную позже — команда /autopost."
                ),
                parse_mode=None,
            )
        return

    await _send_preview(bot, config, content_type, title, text)


async def _retry_later(bot: Bot, config: Config, content_type: str, retries_left: int) -> None:
    """Ждёт 30 минут и пробует сгенерировать автопост ещё раз."""
    await asyncio.sleep(RETRY_DELAY_SEC)
    logger.info("Автопост: повторная попытка генерации (осталось повторов: %d)", retries_left)
    await send_daily_draft(bot, config, content_type, retries_left=retries_left)


async def _send_preview(
    bot: Bot,
    config: Config,
    content_type: str,
    title: str,
    text: str,
    source: dict = None,
) -> None:
    """Генерирует картинку и присылает тренеру превью с кнопками.

    source — исходная надиктовка тренера (если пост из неё):
    {"text": ...} или {"audio": bytes, "mime": ...}. Нужна для «Переделать».
    """
    emoji, label = _labels(content_type)

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

    header = "Пост из твоей надиктовки" if source else f"Пост на сегодня — о {label}"
    sent = await bot.send_message(
        chat_id=config.admin_chat_id,
        text=(
            f"{emoji} {header}.\n"
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
        "source": source,
    }
    logger.info("Автопост-черновик отправлен тренеру: type=%s, title=%r, source=%s",
                content_type, title, "надиктовка" if source else "генерация")


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
    await send_daily_draft(bot, config, content_type, retries_left=0)


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

    source = draft.get("source")
    if source:
        # Пост из надиктовки — заново оформляем те же слова тренера
        title, text = await structure_dictation(
            config.gemini_api_key,
            text=source.get("text"),
            audio=source.get("audio"),
            audio_mime=source.get("mime", "audio/ogg"),
        )
        if not title:
            await bot.send_message(
                chat_id=config.admin_chat_id,
                text=f"⚠️ Не получилось переделать.\n{text}",
                parse_mode=None,
            )
            return
        await _send_preview(bot, config, draft["content_type"], title, text, source=source)
    else:
        await send_daily_draft(bot, config, draft["content_type"], retries_left=0)


# ── Callback: надиктую сам ───────────────────────────────────────

@router.callback_query(F.data == "autopost_dictate")
async def cb_dictate(callback: CallbackQuery):
    """Тренер хочет надиктовать пост сам — ждём голосовое или текст."""
    config = load_config()
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Только для тренера", show_alert=True)
        return

    # Черновик НЕ убираем: можно передумать и опубликовать авто-вариант
    draft = _drafts.get(callback.message.message_id)
    content_type = draft["content_type"] if draft else _today_content_type()
    topic = draft["title"] if draft else None

    _dictation[callback.from_user.id] = {"content_type": content_type}

    topic_line = f"Можно на тему «{topic}», можно на любую свою.\n" if topic else ""
    await callback.message.answer(
        "🎤 Жду голосовое сообщение (или текст).\n"
        f"{topic_line}"
        "Говори как думаешь — я уберу оговорки и повторы, оформлю "
        "в пост и пришлю на проверку.\n\n"
        "Передумал — напиши «отмена».",
        parse_mode=None,
    )
    await callback.answer()


# ── Приём надиктовки (голосовое или текст от тренера) ────────────

def _awaiting_dictation(message: Message) -> bool:
    if (
        message.chat.type != "private"
        or message.from_user is None
        or message.from_user.id not in _dictation
    ):
        return False
    # Команды пропускаем дальше (кроме /cancel) — чтобы /autopost, /stats
    # и прочие работали даже пока ждём надиктовку
    if message.text and message.text.startswith("/"):
        return message.text.strip().lower() == "/cancel"
    return True


@router.message(_awaiting_dictation)
async def on_dictation(message: Message, bot: Bot):
    config = load_config()

    # Отмена
    if message.text and message.text.strip().lower() in ("отмена", "/cancel", "cancel"):
        _dictation.pop(message.from_user.id, None)
        await message.answer("Ок, отменил. Кнопки на автопосте выше по-прежнему работают.")
        return

    state = _dictation.pop(message.from_user.id, None)
    if state is None:
        return
    content_type = state["content_type"]

    # Собираем источник: голос / аудио / текст
    source = None
    if message.voice:
        buf = io.BytesIO()
        await bot.download(message.voice, destination=buf)
        source = {"audio": buf.getvalue(), "mime": "audio/ogg"}
    elif message.audio:
        buf = io.BytesIO()
        await bot.download(message.audio, destination=buf)
        source = {"audio": buf.getvalue(), "mime": message.audio.mime_type or "audio/mpeg"}
    elif message.text:
        source = {"text": message.text}

    if not source:
        _dictation[message.from_user.id] = state  # ждём дальше
        await message.answer("Пришли голосовое сообщение или обычный текст.")
        return

    await message.answer("⏳ Расшифровываю и оформляю (пара минут)...")

    title, text = await structure_dictation(
        config.gemini_api_key,
        text=source.get("text"),
        audio=source.get("audio"),
        audio_mime=source.get("mime", "audio/ogg"),
    )

    if not title:
        _dictation[message.from_user.id] = state  # можно попробовать ещё раз
        await message.answer(
            f"⚠️ Не получилось оформить.\n{text}\n\nПопробуй отправить ещё раз или напиши «отмена».",
            parse_mode=None,
        )
        return

    await _send_preview(bot, config, content_type, title, text, source=source)


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
