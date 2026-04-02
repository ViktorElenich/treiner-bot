"""
Генерация контента через Google Gemini API.
Создаёт посты о питании и статьи о спорте для Telegram-группы.
"""

import io
import logging
from typing import Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ── Промпты для генерации ────────────────────────────────────────

NUTRITION_PROMPT = """Ты — фитнес-тренер Виктор с 13-летним опытом. Пишешь пост в свой Telegram-чат от первого лица.

Напиши короткий пост (150-250 слов) о питании.

Требования:
- Пиши от первого лица, как будто это твой личный опыт и знания
- Тема: практический совет, разбор мифа, полезный продукт, лайфхак, рецепт
- Стиль: простой, живой, как общаешься с друзьями
- Можно делиться личным опытом ("я обычно...", "мои клиенты часто спрашивают...")
- Используй эмодзи в меру (2-3 штуки)
- В конце можно задать вопрос для обсуждения
- НЕ рекламируй тренировки, не предлагай записаться — просто полезная информация
- НЕ используй Markdown-разметку (**, ##, и т.д.)
- Простой текст с переносами строк
- На русском языке
- Каждый раз новая тема, не повторяйся

Формат ответа:
Первая строка — короткий заголовок темы (3-6 слов, без точки в конце)
Пустая строка
Далее — текст поста"""

ARTICLE_PROMPT = """Ты — фитнес-тренер Виктор с 13-летним опытом. Пишешь пост в свой Telegram-чат от первого лица.

Напиши короткую познавательную статью (200-300 слов) о спорте и тренировках.

Требования:
- Пиши от первого лица, как будто делишься своими знаниями и опытом
- Тема: техника упражнений, восстановление, разбор мифов, интересные факты о теле, мотивация, тренировочные лайфхаки
- Стиль: экспертный но дружелюбный, без сложного жаргона
- Можно упоминать случаи из практики ("один мой клиент...", "часто вижу в зале...")
- Используй эмодзи в меру (2-3 штуки)
- НЕ рекламируй свои услуги, не предлагай записаться — просто полезный контент
- НЕ используй Markdown-разметку (**, ##, и т.д.)
- Простой текст с переносами строк
- На русском языке
- Каждый раз новая тема, не повторяйся

Формат ответа:
Первая строка — короткий заголовок темы (3-6 слов, без точки в конце)
Пустая строка
Далее — текст поста"""


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
        lines = raw.split("\n", 2)
        title = lines[0].strip()
        text = lines[2].strip() if len(lines) > 2 else raw
        logger.info("Контент сгенерирован: type=%s, title=%r, length=%d", content_type, title, len(text))
        return title, text

    except Exception as e:
        logger.error("Ошибка генерации контента: %s", e)
        return "", f"❌ Ошибка генерации: {e}"


async def generate_image(topic: str, api_key: str) -> Optional[bytes]:
    """
    Генерирует картинку через Gemini API.

    topic: краткое описание темы поста
    Возвращает bytes изображения или None при ошибке.
    """
    prompt = (
        f"Сгенерируй красивое фото для поста в Telegram о: {topic}. "
        "Стиль: профессиональная фитнес-фотография, тёплые тона, "
        "без текста на картинке, без людей, фокус на еде/спорте/природе."
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        # Ищем изображение в ответе
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                logger.info("Картинка сгенерирована для: %s", topic[:50])
                return part.inline_data.data

        logger.warning("Gemini не вернул изображение")
        return None

    except Exception as e:
        logger.error("Ошибка генерации картинки: %s", e)
        return None
