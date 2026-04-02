"""
Настройки бота.
Все секретные данные (токены, ключи) читаются из переменных окружения.
Локально они хранятся в файле .env, на сервере — задаются в настройках Render.
"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    # Токен бота от @BotFather
    bot_token: str

    # ID чата, куда бот отправляет заявки с сайта (Telegram user ID тренера)
    admin_chat_id: int

    # ЮKassa
    yukassa_shop_id: str
    yukassa_secret_key: str

    # ID закрытого канала (начинается с -100...)
    channel_id: int

    # ID групп по тарифам
    group_general_id: int   # Общий чат (бесплатный)
    group_start_id: int     # СТАРТ — 2 000 ₽/мес
    group_progress_id: int  # ПРОГРЕСС — 3 500 ₽/мес
    group_result_id: int    # РЕЗУЛЬТАТ — 6 000 ₽/мес

    # Google Gemini API
    gemini_api_key: str

    # ID топиков в общем чате (форум-группе)
    topic_nutrition_id: int   # Тема "Питание"
    topic_articles_id: int    # Тема "Статьи о спорте"

    # Секретный ключ для проверки заявок с сайта
    # (чтобы никто посторонний не мог слать фейковые заявки)
    webhook_secret: str

    # URL бота на Render (для настройки webhook Telegram)
    webhook_base_url: str

    # Порт веб-сервера (Render задаёт автоматически)
    port: int


def load_config() -> Config:
    """Загрузить настройки из переменных окружения."""
    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        admin_chat_id=int(os.environ["ADMIN_CHAT_ID"]),
        yukassa_shop_id=os.environ.get("YUKASSA_SHOP_ID", ""),
        yukassa_secret_key=os.environ.get("YUKASSA_SECRET_KEY", ""),
        channel_id=int(os.environ.get("CHANNEL_ID", "0")),
        group_general_id=int(os.environ.get("GROUP_GENERAL_ID", "0")),
        group_start_id=int(os.environ.get("GROUP_START_ID", "0")),
        group_progress_id=int(os.environ.get("GROUP_PROGRESS_ID", "0")),
        group_result_id=int(os.environ.get("GROUP_RESULT_ID", "0")),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        topic_nutrition_id=int(os.environ.get("TOPIC_NUTRITION_ID", "0")),
        topic_articles_id=int(os.environ.get("TOPIC_ARTICLES_ID", "0")),
        webhook_secret=os.environ.get("WEBHOOK_SECRET", "default-secret-change-me"),
        webhook_base_url=os.environ.get("WEBHOOK_BASE_URL", ""),
        port=int(os.environ.get("PORT", "8080")),
    )
