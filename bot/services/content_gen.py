"""
Генерация контента через Google Gemini API.
Создаёт посты о питании и статьи о спорте для Telegram-группы.
Генерация картинок — через Kie AI (Nano Banana 2).
"""

import asyncio
import json
import logging
from typing import Optional

import aiohttp
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ── Промпты для генерации ────────────────────────────────────────

# Расшифровки голосовых Виктора — образцы его живой речи.
# Модель копирует манеру: разговорные связки, обращение на «ты»,
# объяснение по шагам с конкретикой.
STYLE_SAMPLES = """Вот как Виктор объясняет на самом деле (расшифровки его голосовых, копируй эту манеру):

Пример 1, про питание:
«Есть такое мнение, что когда человек находится в сильном дефиците калорий, тело начинает запасаться. То есть оно находится в стрессе и не отдаёт вес. Для этого нужно сделать такую манипуляцию: сначала выйти в профицит, чтобы тело поняло, что все питательные вещества поступают, оно не находится в дефиците калорий. Стресса нет, и, соответственно, оно проще начнёт избавляться от жировой прослойки. Затем понемногу делать дефицит калорий, то есть 10% от калоража снижаем, и тело плавно начнёт худеть. Но при этом мы не увеличиваем физическую нагрузку, оставляем ту, которая есть, просто начинаем потихонечку снижать калорийность.»

Пример 2, про тренировки:
«Поначалу, когда новичок приходит только в зал, это нормальная адаптация мышц к тренировкам, что они болят. Чем больше клиент начинает ходить на тренировки, тем не так уже сильно болят мышцы. Но они в любом случае могут болеть, иногда даже больше, если добавили какие-то новые упражнения. Если, допустим, у тебя не болят мышцы, это не значит, что ты плохо потренировался. Это значит, что нагрузка была правильно подобрана, и, соответственно, тело хорошо восстановилось. Плюс тут влияют ещё процессы восстановления после тренировки: как ты спишь, как ты питаешься, как отдыхаешь.»"""

COMMON_RULES = """Как писать (главное — чтобы текст звучал как живой человек, а не нейросеть):
- Копируй манеру Виктора из примеров: простая разговорная речь, связки «то есть», «соответственно», «допустим», «смотри», обращение к читателю на «ты»
- Одна конкретная мысль на пост. Не пытайся охватить всё — лучше объясни одну вещь по шагам, с конкретными цифрами и примерами
- Это статья от эксперта, а не история про клиентов. НЕ упоминай клиентов, НЕ начинай пост с «Мои клиенты...», «Ко мне часто приходят...», «Мне часто пишут...» и подобного. Начинай сразу с сути: с утверждения, факта, разбора мифа или частого заблуждения
- Разнообразь начала постов — они не должны строиться по одному шаблону
- Лёгкие повторы и неидеальные фразы — это нормально, так говорят живые люди
- ЗАПРЕЩЕНЫ типичные обороты нейросетей: «Давайте разберёмся», «Итак», «Подведём итог», «Не секрет, что», «Важно отметить», «В современном мире», «Спойлер», «А теперь к главному», «Готовы? Поехали»
- НЕ делай идеальную структуру «вступление — три пункта — вывод» и списки ровно из трёх пунктов
- Вопрос в конце можно задать, но не в каждом посте — иногда просто закончи мыслью
- Эмодзи: максимум 1-2 на весь пост, можно вообще без них
- НЕ используй Markdown-разметку (**, ##, списки с * или -)
- НЕ рекламируй услуги, не зови записаться — просто полезная информация
- На русском языке
- Каждый раз новая тема, не повторяйся

Формат ответа:
Первая строка — короткий заголовок темы (3-6 слов, без точки в конце)
Пустая строка
Далее — текст поста"""

NUTRITION_PROMPT = f"""Ты — фитнес-тренер Виктор с 13-летним опытом. Пишешь пост в свой Telegram-чат от первого лица.

{STYLE_SAMPLES}

Напиши короткий пост (120-200 слов) о питании.
Тема: практический совет, разбор мифа, полезный продукт, лайфхак, простой рецепт.

{COMMON_RULES}"""

ARTICLE_PROMPT = f"""Ты — фитнес-тренер Виктор с 13-летним опытом. Пишешь пост в свой Telegram-чат от первого лица.

{STYLE_SAMPLES}

Напиши короткий пост (150-250 слов) о спорте и тренировках.
Тема: техника упражнений, восстановление, разбор мифа, как устроено тело, тренировочный лайфхак, ответ на частый вопрос новичка.

{COMMON_RULES}"""


DICTATION_PROMPT = """Ты — редактор фитнес-тренера Виктора. Он надиктовал черновик поста для своего Telegram-чата{topic_hint}.

Преобразуй надиктовку в готовый пост:
- Убери слова-паразиты, оговорки, случайные повторы, обрывки фраз
- Разбей на абзацы, поправь грамматику
- СОХРАНИ манеру и формулировки Виктора: его связки («то есть», «соответственно», «допустим», «смотри»), обращение на «ты», порядок мыслей
- НИЧЕГО не добавляй от себя — никаких фактов, советов и выводов, которых нет в надиктовке
- НЕ используй Markdown-разметку (**, ##, списки с * или -)
- Эмодзи: максимум 1-2, только если уместно
- На русском языке

Формат ответа:
Первая строка — короткий заголовок темы (3-6 слов, без точки в конце)
Пустая строка
Далее — текст поста"""


def _parse_title_and_text(raw: str) -> tuple:
    """Первая строка — заголовок, дальше — текст поста."""
    lines = raw.split("\n", 2)
    title = lines[0].strip()
    text = lines[2].strip() if len(lines) > 2 else raw
    return title, text


def _build_prompt(base_prompt: str, past_titles: list) -> str:
    if not past_titles:
        return base_prompt
    titles_str = ", ".join(past_titles)
    return base_prompt + f"\n\nУже были темы (не повторяй): {titles_str}"


async def generate_content(
    content_type: str,
    api_key: str,
    past_titles: Optional[list] = None,
) -> tuple:
    """
    Генерирует текст через Gemini API.

    content_type: "nutrition" или "article"
    past_titles: список заголовков предыдущих постов для исключения повторов
    Возвращает (title, text) или ("", "❌ Ошибка...") при ошибке.
    """
    base = NUTRITION_PROMPT if content_type == "nutrition" else ARTICLE_PROMPT
    prompt = _build_prompt(base, past_titles or [])

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
        )
        raw = response.text.strip()
        title, text = _parse_title_and_text(raw)
        logger.info("Контент сгенерирован: type=%s, title=%r, length=%d", content_type, title, len(text))
        return title, text

    except Exception as e:
        logger.error("Ошибка генерации контента: %s", e)
        return "", f"❌ Ошибка генерации: {e}"


async def structure_dictation(
    api_key: str,
    text: Optional[str] = None,
    audio: Optional[bytes] = None,
    audio_mime: str = "audio/ogg",
    topic: Optional[str] = None,
) -> tuple:
    """
    Превращает надиктовку тренера (голосовое или текст) в оформленный пост.

    text: надиктовка текстом, ИЛИ
    audio: байты голосового сообщения (Gemini расшифровывает сам)
    topic: тема-подсказка (не обязательно)
    Возвращает (title, text) или ("", "❌ Ошибка...") при ошибке.
    """
    topic_hint = f" на тему «{topic}»" if topic else ""
    prompt = DICTATION_PROMPT.format(topic_hint=topic_hint)

    contents = [prompt]
    if audio:
        contents.append(types.Part.from_bytes(data=audio, mime_type=audio_mime))
    else:
        contents.append(f"Надиктовка (текстом):\n{text}")

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=contents,
        )
        raw = response.text.strip()
        title, post_text = _parse_title_and_text(raw)
        logger.info("Надиктовка оформлена: title=%r, length=%d", title, len(post_text))
        return title, post_text

    except Exception as e:
        logger.error("Ошибка оформления надиктовки: %s", e)
        return "", f"❌ Ошибка оформления: {e}"


async def generate_image(topic: str, kie_api_key: str) -> Optional[bytes]:
    """
    Генерирует картинку через Kie AI API (Nano Banana 2).

    topic: краткое описание темы поста
    Возвращает bytes изображения или None при ошибке.
    """
    prompt = (
        f"A vivid, aesthetic photograph that directly illustrates the topic: \"{topic}\". "
        "The image should clearly relate to this specific subject. "
        "Style: clean, professional, warm natural lighting, shallow depth of field. "
        "No text, no watermarks, no people. High quality stock photo look."
    )

    headers = {
        "Authorization": f"Bearer {kie_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "nano-banana-2",
        "input": {
            "prompt": prompt,
            "aspect_ratio": "1:1",
            "resolution": "1K",
            "output_format": "jpg",
        },
    }

    try:
        async with aiohttp.ClientSession() as session:
            for retry in range(2):
                # 1. Создаём задачу
                async with session.post(
                    "https://api.kie.ai/api/v1/jobs/createTask",
                    json=payload,
                    headers=headers,
                ) as resp:
                    result = await resp.json()
                    if result.get("code") != 200:
                        logger.error("Kie AI createTask ошибка: %s", result)
                        return None
                    task_id = result["data"]["taskId"]

                # 2. Поллим результат (макс ~120 сек)
                for attempt in range(40):
                    await asyncio.sleep(3)
                    async with session.get(
                        f"https://api.kie.ai/api/v1/jobs/recordInfo?taskId={task_id}",
                        headers=headers,
                    ) as resp:
                        result = await resp.json()
                        state = result.get("data", {}).get("state", "")

                        if state == "success":
                            result_json = json.loads(result["data"]["resultJson"])
                            image_url = result_json["resultUrls"][0]
                            # 3. Скачиваем картинку
                            async with session.get(image_url) as img_resp:
                                image_bytes = await img_resp.read()
                                logger.info("Картинка сгенерирована для: %s", topic[:50])
                                return image_bytes

                        elif state == "fail":
                            fail_msg = result.get("data", {}).get("failMsg", "unknown")
                            logger.error("Kie AI задача провалена: %s", fail_msg)
                            return None

                logger.warning("Kie AI таймаут: задача не завершилась за 120 сек (попытка %d/2)", retry + 1)

            return None

    except Exception as e:
        logger.error("Ошибка генерации картинки: %s", e)
        return None
