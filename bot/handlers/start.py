"""
Обработчик команды /start и навигации по меню.
Это то, что видит клиент когда открывает бота.
"""

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery

from bot.keyboards.inline import (
    main_menu_keyboard,
    tariffs_keyboard,
    tariff_detail_keyboard,
    back_to_menu_keyboard,
)
from bot.database import add_to_waitlist

router = Router()

# ── Информация о тарифах ──────────────────────────────────────────

TARIFFS = {
    "start": {
        "name": "СТАРТ",
        "price": 2000,
        "emoji": "🟢",
        "description": (
            "🟢 <b>СТАРТ — 2 000 ₽/мес</b>\n\n"
            "Идеально для начинающих:\n"
            "• Программа тренировок на месяц\n"
            "• Тренировки для дома\n"
            "• С минимальным инвентарём\n"
            "• 3–5 раз в неделю\n"
            "• Общий чат участников\n"
            "• Доступ к закрытому каналу"
        ),
    },
    "progress": {
        "name": "ПРОГРЕСС",
        "price": 3500,
        "emoji": "🔥",
        "description": (
            "🔥 <b>ПРОГРЕСС — 3 500 ₽/мес</b> (хит)\n\n"
            "Для тех, кто хочет больше:\n"
            "• Всё из тарифа СТАРТ\n"
            "• Тренировки для дома и зала\n"
            "• Советы по питанию\n"
            "• Чат + AI-бот 24/7\n"
            "• Ответы на вопросы в чате"
        ),
    },
    "result": {
        "name": "РЕЗУЛЬТАТ",
        "price": 6000,
        "emoji": "🏆",
        "description": (
            "🏆 <b>РЕЗУЛЬТАТ — 6 000 ₽/мес</b>\n\n"
            "Полное ведение:\n"
            "• Всё из тарифа ПРОГРЕСС\n"
            "• Персональный план питания\n"
            "• Проверка техники по видео\n"
            "• Корректировка программы по прогрессу\n"
            "• Личный чат с тренером"
        ),
    },
}

# ── Приветственное сообщение ──────────────────────────────────────

WELCOME_TEXT = (
    "👋 <b>Привет! Я — бот тренера Виктора Еленич.</b>\n\n"
    "Здесь ты можешь:\n"
    "• Выбрать и оплатить онлайн-программу тренировок\n"
    "• Записаться на бесплатную консультацию\n"
    "• Узнать больше о тренере\n\n"
    "Выбери, что тебя интересует 👇"
)

ABOUT_TEXT = (
    "<b>Виктор Еленич</b> — персональный тренер\n\n"
    "📅 13+ лет опыта\n"
    "👥 200+ довольных клиентов\n"
    "🏋️ 3 направления тренировок\n\n"
    "<b>Направления:</b>\n"
    "• Персональные тренировки (один на один) — 3 500 ₽/час\n"
    "• Тайский бокс для девушек (до 6 чел.) — 2 200 ₽/час\n"
    "• Функциональный тренинг (до 8 чел.) — 1 600 ₽/час\n\n"
    "🌐 <a href='https://viktorelenich.github.io/treiner-web/'>Сайт</a> · "
    "📱 <a href='https://t.me/ViktorElenich'>Telegram</a>"
)

CONSULTATION_TEXT = (
    "📋 <b>Бесплатная консультация</b>\n\n"
    "Напиши мне напрямую — разберём твою ситуацию, "
    "подберём направление и составим план:\n\n"
    "👉 @ViktorElenich\n\n"
    "Или оставь заявку на сайте — свяжусь в течение 2 часов."
)


# ── Команда /start ────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    """
    Обработка команды /start.
    Если пришёл deep-link (например /start tariff_progress),
    сразу показываем нужный тариф.
    """
    # Проверяем deep-link параметр (из кнопок на сайте)
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        param = args[1]  # например "tariff_progress" или "waitlist"

        # Запись в лист ожидания
        if param == "waitlist":
            user = message.from_user
            is_new = await add_to_waitlist(
                user_id=user.id,
                username=user.username or "",
                full_name=user.full_name or "",
            )
            if is_new:
                await message.answer(
                    "📋 <b>Ты в листе ожидания!</b>\n\n"
                    "Как только откроется набор на онлайн-программы, "
                    "я сразу пришлю тебе уведомление.\n\n"
                    "А пока — можешь посмотреть тарифы 👇",
                    reply_markup=tariffs_keyboard(),
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    "✅ <b>Ты уже в листе ожидания!</b>\n\n"
                    "Я пришлю уведомление, когда откроется набор.\n\n"
                    "Можешь посмотреть тарифы 👇",
                    reply_markup=tariffs_keyboard(),
                    parse_mode="HTML",
                )
            return

        # Переход к конкретному тарифу
        if param.startswith("tariff_"):
            tariff_id = param.replace("tariff_", "")  # "progress"
            if tariff_id in TARIFFS:
                tariff = TARIFFS[tariff_id]
                await message.answer(
                    tariff["description"],
                    reply_markup=tariff_detail_keyboard(tariff_id),
                    parse_mode="HTML",
                )
                return

    # Обычный /start — показываем приветствие
    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


# ── Команда /chatid — показать ID текущего чата ──────────────────

@router.message(Command("chatid"))
async def cmd_chatid(message: Message):
    """Показывает ID чата, в котором написана команда."""
    text = (
        f"📌 <b>ID этого чата:</b>\n\n"
        f"<b>Название:</b> {message.chat.title or 'Личный чат'}\n"
        f"<b>ID:</b> <code>{message.chat.id}</code>"
    )
    if message.message_thread_id:
        text += f"\n<b>ID топика:</b> <code>{message.message_thread_id}</code>"
    await message.answer(text, parse_mode="HTML")


# ── Помощник: показать ID пересланного сообщения ──────────────────

@router.message(F.forward_from_chat)
async def show_forwarded_chat_id(message: Message):
    """
    Если переслать сообщение из канала — бот покажет ID канала.
    Это нужно для настройки CHANNEL_ID.
    """
    chat = message.forward_from_chat
    await message.answer(
        f"📌 <b>Информация о чате:</b>\n\n"
        f"<b>Название:</b> {chat.title}\n"
        f"<b>ID:</b> <code>{chat.id}</code>\n\n"
        f"Скопируй этот ID — он нужен для настройки бота.",
        parse_mode="HTML",
    )


# ── Навигация по меню (нажатия кнопок) ────────────────────────────

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    """Кнопка "Назад в меню"."""
    await callback.message.edit_text(
        WELCOME_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "show_tariffs")
async def show_tariffs(callback: CallbackQuery):
    """Показать список тарифов."""
    await callback.message.edit_text(
        "💪 <b>Онлайн-программы тренировок</b>\n\n"
        "Выбери подходящий тариф:",
        reply_markup=tariffs_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tariff_"))
async def show_tariff_detail(callback: CallbackQuery):
    """Показать описание конкретного тарифа."""
    tariff_id = callback.data.replace("tariff_", "")  # "start", "progress", "result"
    tariff = TARIFFS.get(tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await callback.message.edit_text(
        tariff["description"],
        reply_markup=tariff_detail_keyboard(tariff_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "about")
async def show_about(callback: CallbackQuery):
    """Информация о тренере."""
    await callback.message.edit_text(
        ABOUT_TEXT,
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "consultation")
async def show_consultation(callback: CallbackQuery):
    """Записаться на консультацию."""
    await callback.message.edit_text(
        CONSULTATION_TEXT,
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()



# Обработчик pay_* перенесён в bot/handlers/payments.py
