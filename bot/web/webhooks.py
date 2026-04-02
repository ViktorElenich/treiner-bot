"""
Приём заявок с сайта.
Когда клиент заполняет форму на сайте, данные отправляются сюда,
и бот пересылает их тренеру в Telegram.
"""

import logging
from aiohttp import web
from aiogram import Bot

from bot.database import save_lead

logger = logging.getLogger(__name__)


def setup_site_webhooks(app: web.Application, bot: Bot, config) -> None:
    """Регистрирует маршруты для приёма заявок с сайта."""

    async def handle_lead(request: web.Request) -> web.Response:
        """
        Приём заявки с формы "Запишись на разбор ситуации".
        Ожидает JSON: { "name": "Имя", "phone": "+7..." }
        """
        # Проверяем секретный ключ
        secret = request.headers.get("X-Webhook-Secret", "")
        if secret != config.webhook_secret:
            return web.json_response(
                {"error": "Unauthorized"},
                status=403,
                headers=_cors_headers(),
            )

        try:
            data = await request.json()
            name = data.get("name", "Не указано")
            phone = data.get("phone", "Не указано")

            # Отправляем заявку тренеру
            text = (
                "📩 <b>Новая заявка с сайта!</b>\n\n"
                f"<b>Форма:</b> Разбор ситуации\n"
                f"<b>Имя:</b> {_escape(name)}\n"
                f"<b>Телефон/Telegram:</b> {_escape(phone)}"
            )
            await bot.send_message(
                chat_id=config.admin_chat_id,
                text=text,
                parse_mode="HTML",
            )

            await save_lead(source="lead", name=name, phone=phone)
            logger.info("Lead заявка от %s отправлена тренеру", name)
            return web.json_response(
                {"status": "ok"},
                headers=_cors_headers(),
            )

        except Exception as e:
            logger.error("Ошибка обработки lead-заявки: %s", e)
            return web.json_response(
                {"error": "Internal error"},
                status=500,
                headers=_cors_headers(),
            )

    async def handle_consultation(request: web.Request) -> web.Response:
        """
        Приём заявки с формы "Начни сегодня" (консультация).
        Ожидает JSON: { "name": "Имя", "phone": "+7...",
                        "direction": "personal", "goal": "Похудеть" }
        """
        secret = request.headers.get("X-Webhook-Secret", "")
        if secret != config.webhook_secret:
            return web.json_response(
                {"error": "Unauthorized"},
                status=403,
                headers=_cors_headers(),
            )

        try:
            data = await request.json()
            name = data.get("name", "Не указано")
            phone = data.get("phone", "Не указано")
            direction = data.get("direction", "Не выбрано")
            goal = data.get("goal", "Не указана")

            # Переводим код направления в читаемый текст
            direction_names = {
                "personal": "Персональные тренировки",
                "boxing": "Тайский бокс для девушек",
                "functional": "Функциональный тренинг",
                "online-start": "Онлайн СТАРТ (2 000 ₽/мес)",
                "online-progress": "Онлайн ПРОГРЕСС (3 500 ₽/мес)",
                "online-result": "Онлайн РЕЗУЛЬТАТ (6 000 ₽/мес)",
                "unknown": "Не знает — хочет разобраться",
            }
            direction_text = direction_names.get(direction, direction)

            text = (
                "📩 <b>Новая заявка с сайта!</b>\n\n"
                f"<b>Форма:</b> Консультация\n"
                f"<b>Имя:</b> {_escape(name)}\n"
                f"<b>Телефон:</b> {_escape(phone)}\n"
                f"<b>Направление:</b> {_escape(direction_text)}\n"
                f"<b>Цель:</b> {_escape(goal) if goal else 'Не указана'}"
            )
            await bot.send_message(
                chat_id=config.admin_chat_id,
                text=text,
                parse_mode="HTML",
            )

            await save_lead(
                source="consultation", name=name, phone=phone,
                direction=direction, goal=goal,
            )
            logger.info("Consultation заявка от %s отправлена тренеру", name)
            return web.json_response(
                {"status": "ok"},
                headers=_cors_headers(),
            )

        except Exception as e:
            logger.error("Ошибка обработки consultation-заявки: %s", e)
            return web.json_response(
                {"error": "Internal error"},
                status=500,
                headers=_cors_headers(),
            )

    async def handle_options(request: web.Request) -> web.Response:
        """
        Обработка preflight-запросов (CORS).
        Браузер отправляет OPTIONS перед POST, чтобы проверить разрешения.
        """
        return web.Response(
            status=200,
            headers=_cors_headers(),
        )

    # Регистрируем маршруты
    app.router.add_post("/api/lead", handle_lead)
    app.router.add_post("/api/consultation", handle_consultation)
    # CORS preflight
    app.router.add_options("/api/lead", handle_options)
    app.router.add_options("/api/consultation", handle_options)


def _cors_headers() -> dict:
    """
    CORS-заголовки — разрешают сайту отправлять запросы к боту.
    Без них браузер блокирует запросы с другого домена.
    """
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Webhook-Secret",
    }


def setup_yukassa_webhook(app: web.Application, bot: Bot, config) -> None:
    """Регистрирует маршрут для приёма уведомлений от ЮKassa."""

    async def handle_yukassa_notification(request: web.Request) -> web.Response:
        """
        ЮKassa присылает сюда уведомление когда клиент оплатил.
        Мы проверяем платёж через API (защита от подделки)
        и выдаём клиенту доступ к каналу.
        """
        try:
            data = await request.json()
            event = data.get("event")

            # Нас интересует только успешная оплата
            if event != "payment.succeeded":
                return web.json_response({"status": "ok"})

            payment_object = data.get("object", {})
            payment_id = payment_object.get("id")

            if not payment_id:
                return web.json_response({"error": "no payment_id"}, status=400)

            # Проверяем платёж через API ЮKassa (защита от поддельных webhook)
            from bot.services.yukassa import check_payment
            verified = await check_payment(payment_id)

            if verified["status"] != "succeeded":
                logger.warning(
                    "Webhook: платёж %s не succeeded (status=%s)",
                    payment_id,
                    verified["status"],
                )
                return web.json_response({"status": "ok"})

            metadata = verified["metadata"]
            user_id = int(metadata.get("user_id", 0))
            tariff_id = metadata.get("tariff", "")

            if not user_id or not tariff_id:
                logger.error("Нет metadata в платеже %s", payment_id)
                return web.json_response({"status": "ok"})

            # Обрабатываем успешную оплату
            from bot.handlers.payments import process_successful_payment
            await process_successful_payment(
                bot=bot,
                callback=None,
                payment_id=payment_id,
                user_id=user_id,
                username=metadata.get("username", ""),
                full_name=metadata.get("full_name", ""),
                tariff_id=tariff_id,
            )

            logger.info("ЮKassa webhook обработан: payment=%s", payment_id)
            return web.json_response({"status": "ok"})

        except Exception as e:
            logger.error("Ошибка обработки ЮKassa webhook: %s", e)
            # Всегда возвращаем 200 — иначе ЮKassa будет повторять запрос
            return web.json_response({"status": "ok"})

    app.router.add_post("/api/yukassa/webhook", handle_yukassa_notification)


def _escape(text: str) -> str:
    """Экранирование HTML-символов для безопасности."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
