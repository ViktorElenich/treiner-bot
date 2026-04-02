"""
Главный файл бота — точка запуска.
Запускает Telegram-бота и веб-сервер для приёма заявок с сайта.

Два режима работы:
- Локально (без WEBHOOK_BASE_URL): polling — бот сам опрашивает Telegram
- На сервере (с WEBHOOK_BASE_URL): webhook — Telegram присылает обновления боту
"""

import asyncio
import logging

from dotenv import load_dotenv
load_dotenv()  # Загружает настройки из файла .env

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)

from bot.config import load_config
from bot.database import init_db
from bot.handlers.content import router as content_router
from bot.handlers.moderation import router as moderation_router
from bot.handlers.payments import router as payments_router
from bot.handlers.start import router as start_router
from bot.services.yukassa import configure as configure_yukassa
from bot.web.webhooks import setup_site_webhooks, setup_yukassa_webhook

# Настраиваем логирование (вывод сообщений о работе бота)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot, config) -> None:
    """Вызывается при запуске бота — инициализирует БД и устанавливает webhook."""
    await init_db()

    webhook_url = f"{config.webhook_base_url}/webhook"
    await bot.set_webhook(
        url=webhook_url,
        secret_token=config.webhook_secret,
        drop_pending_updates=True,
    )
    logger.info("Webhook установлен: %s", webhook_url)

    # Показываем информацию о боте
    me = await bot.get_me()
    logger.info("Бот запущен: @%s (%s)", me.username, me.full_name)


async def on_shutdown(bot: Bot) -> None:
    """Вызывается при остановке бота — удаляет webhook."""
    await bot.delete_webhook()
    logger.info("Webhook удалён, бот остановлен")


def create_bot_and_dispatcher():
    """Создаёт бота и диспетчер."""
    config = load_config()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()

    # moderation_router первый — ловит все сообщения в группах
    dp.include_router(moderation_router)
    # content_router — генерация и публикация контента
    dp.include_router(content_router)
    # payments_router ПЕРЕД start_router — чтобы pay_* обрабатывался в payments.py
    dp.include_router(payments_router)
    dp.include_router(start_router)

    # Настраиваем ЮKassa (если ключи заданы)
    if config.yukassa_shop_id and config.yukassa_secret_key:
        configure_yukassa(config.yukassa_shop_id, config.yukassa_secret_key)

    return bot, dp, config


def run_webhook():
    """Запуск в режиме webhook (для сервера Render)."""
    bot, dp, config = create_bot_and_dispatcher()

    # Создаём веб-приложение
    app = web.Application()

    # Настраиваем приём обновлений от Telegram через webhook
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=config.webhook_secret,
    )
    webhook_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)

    # Инициализация БД и установка webhook при старте aiohttp
    async def _on_startup(_app):
        await on_startup(bot, config)

    async def _on_shutdown(_app):
        await on_shutdown(bot)

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)

    # Настраиваем приём заявок с сайта
    setup_site_webhooks(app, bot, config)

    # Настраиваем приём уведомлений от ЮKassa
    setup_yukassa_webhook(app, bot, config)

    # Health-check (Render проверяет, жив ли сервер)
    app.router.add_get("/health", health_check)

    logger.info("Запускаю webhook-сервер на порту %d...", config.port)
    web.run_app(app, host="0.0.0.0", port=config.port)


async def run_polling():
    """Запуск в режиме polling (для локальной разработки)."""
    bot, dp, config = create_bot_and_dispatcher()

    # Инициализируем базу данных
    await init_db()

    # Удаляем старый webhook (если был)
    await bot.delete_webhook(drop_pending_updates=True)

    me = await bot.get_me()
    logger.info("Бот запущен в режиме polling: @%s (%s)", me.username, me.full_name)
    logger.info("Нажми Ctrl+C для остановки")

    # Запускаем polling — бот будет работать пока не нажмёшь Ctrl+C
    await dp.start_polling(bot)


async def health_check(request: web.Request) -> web.Response:
    """Проверка здоровья сервера — Render пингует этот адрес."""
    return web.json_response({"status": "ok"})


def main():
    """Точка входа — выбирает режим запуска."""
    config = load_config()

    if config.webhook_base_url:
        # На сервере — webhook-режим
        run_webhook()
    else:
        # Локально — polling-режим
        logger.info("WEBHOOK_BASE_URL не задан — запускаю в режиме polling...")
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
