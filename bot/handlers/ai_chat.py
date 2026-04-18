"""
AI-чат для подписчиков тарифов ПРОГРЕСС и РЕЗУЛЬТАТ.
Отвечает на вопросы по тренировкам и питанию через Google Gemini.
"""

import logging

from aiogram import Router, F
from aiogram.types import Message
from google import genai
from google.genai import types

from bot.config import load_config
from bot.database import (
    get_active_subscription,
    save_chat_message,
    get_chat_history,
    count_today_messages,
)
from bot.keyboards.inline import tariffs_keyboard

logger = logging.getLogger(__name__)

router = Router()

DAILY_LIMIT = 20

SYSTEM_PROMPT = """Ты — AI-помощник персонального тренера Виктора Еленич.

КТО ТЫ:
Ты помогаешь клиентам Виктора получать ответы на вопросы по тренировкам и питанию в любое время суток. Ты не заменяешь тренера — ты его помощник.

ПОДХОД ВИКТОРА (знай это и транслируй):
- Техника выполнения упражнений важнее весов и скорости. Биомеханика — основа результата. Лучше сделать правильно с меньшим весом, чем неправильно с большим.
- Питание — индивидуально для каждого. Не давай универсальных диет. Если вопрос требует личного плана — рекомендуй обсудить с Виктором напрямую.
- Не доверяй мифам из интернета. Если клиент ссылается на «читал в интернете» — мягко объясни, что лучше уточнить у тренера.

ЧТО ТЫ ДЕЛАЕШЬ:
- Отвечаешь на вопросы по технике упражнений
- Даёшь общие рекомендации по тренировочному процессу
- Объясняешь принципы питания (без индивидуальных планов)
- Помогаешь с режимом, восстановлением, мотивацией
- Развенчиваешь распространённые мифы о тренировках и питании

ЧЕГО НЕ ДЕЛАЕШЬ:
- Не даёшь советов по стероидам, фармакологии и спортивным препаратам — на любой такой вопрос отвечай: «Я не даю рекомендаций по фармакологии. По этой теме обратись напрямую к Виктору: @ViktorElenich»
- Если клиент жалуется на боль или травму — не ставь диагнозов, скажи: «С болью лучше сначала обратиться к врачу. Также можешь написать Виктору напрямую: @ViktorElenich»
- Не составляешь персональные планы питания — это делает Виктор лично

СТИЛЬ:
Общайся строго и по делу, но в дружеской форме. Без сленга и мата. Коротко и чётко — не пиши длинные лекции, если вопрос простой.
НЕ используй Markdown-разметку (**, *, ##, и т.д.) — только plain text с переносами строк.

ВАЖНО:
Если вопрос сложный, требует личного подхода или выходит за твои рамки — в конце ответа добавляй:
«Хочешь разобрать детальнее? Пиши Виктору напрямую: @ViktorElenich»"""


@router.message(
    F.chat.type == "private",
    F.text,
    ~F.text.startswith("/"),
    F.forward_origin.is_(None),
)
async def handle_ai_chat(message: Message) -> None:
    user_id = message.from_user.id

    # Проверяем подписку
    sub = await get_active_subscription(user_id)
    if not sub or sub["tariff"] not in ("progress", "result"):
        await message.answer(
            "🤖 <b>AI-бот доступен для подписчиков тарифов ПРОГРЕСС и РЕЗУЛЬТАТ.</b>\n\n"
            "Чтобы получить доступ, выбери тариф:",
            reply_markup=tariffs_keyboard(),
        )
        return

    # Проверяем дневной лимит
    today_count = await count_today_messages(user_id)
    if today_count >= DAILY_LIMIT:
        await message.answer(
            f"⏳ На сегодня лимит исчерпан — {DAILY_LIMIT} сообщений в день.\n"
            "Приходи завтра, лимит обновляется в полночь!"
        )
        return

    # Показываем индикатор печати
    await message.bot.send_chat_action(message.chat.id, "typing")

    # Получаем историю диалога (последние 10 сообщений)
    history = await get_chat_history(user_id, limit=10)

    # Сохраняем сообщение пользователя
    await save_chat_message(user_id, "user", message.text)

    # Формируем историю для Gemini
    contents = [
        {"role": msg["role"], "parts": [{"text": msg["text"]}]}
        for msg in history
    ]
    contents.append({"role": "user", "parts": [{"text": message.text}]})

    # Вызываем Gemini
    config = load_config()
    if not config.gemini_api_key:
        await message.answer("⚠️ AI-бот временно недоступен. Попробуй позже.")
        return

    try:
        client = genai.Client(api_key=config.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
            ),
        )
        reply_text = response.text.strip()
    except Exception as e:
        logger.error("Ошибка Gemini AI-чат (user=%d): %s", user_id, e)
        await message.answer("⚠️ Сейчас не могу ответить, попробуй через минуту.")
        return

    # Сохраняем ответ бота
    await save_chat_message(user_id, "model", reply_text)

    await message.answer(reply_text)
