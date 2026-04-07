"""
Приём заявок с сайта.
Когда клиент заполняет форму на сайте, данные отправляются сюда,
и бот пересылает их тренеру в Telegram.

Защита:
- CORS: только viktor-trainer.ru (не *)
- Rate limiting: 5 заявок в минуту с одного IP
- Валидация: длина полей, удаление HTML-тегов
- Экранирование: HTML-символы в Telegram-сообщениях
"""

import logging
import re
import time
from collections import defaultdict

from aiohttp import web
from aiogram import Bot

from bot.database import save_lead

logger = logging.getLogger(__name__)

# ── Разрешённые домены для CORS ─────────────────────────────────
ALLOWED_ORIGINS = [
    "https://viktor-trainer.ru",
    "https://www.viktor-trainer.ru",
    "http://localhost:8080",  # Для локальной разработки
]

# ── Rate limiting ────────────────────────────────────────────────
# Максимум 5 заявок в минуту с одного IP
RATE_LIMIT = 5
RATE_WINDOW = 60  # секунд
_rate_store: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(ip: str) -> bool:
    """Проверяет, не превышен ли лимит запросов с IP."""
    now = time.time()
    # Убираем старые записи
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < RATE_WINDOW]
    if len(_rate_store[ip]) >= RATE_LIMIT:
        return True
    _rate_store[ip].append(now)
    return False


# ── Санитизация ввода ────────────────────────────────────────────
def _sanitize(text: str, max_len: int = 200) -> str:
    """Удаляет HTML-теги и ограничивает длину."""
    clean = re.sub(r"<[^>]*>", "", text)
    return clean.strip()[:max_len]


def _get_origin(request: web.Request) -> str:
    """Получает Origin из заголовков запроса."""
    return request.headers.get("Origin", "")


def _cors_headers(origin: str = "") -> dict:
    """
    CORS-заголовки — разрешают только доверенным доменам.
    """
    # Если Origin в списке разрешённых — отвечаем им же
    allowed = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
    return {
        "Access-Control-Allow-Origin": allowed,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def setup_site_webhooks(app: web.Application, bot: Bot, config) -> None:
    """Регистрирует маршруты для приёма заявок с сайта."""

    async def handle_lead(request: web.Request) -> web.Response:
        """
        Приём заявки с формы "Запишись на разбор ситуации".
        Ожидает JSON: { "name": "Имя", "phone": "+7..." }
        """
        origin = _get_origin(request)
        headers = _cors_headers(origin)

        # Проверяем Origin
        if origin and origin not in ALLOWED_ORIGINS:
            return web.json_response(
                {"error": "Forbidden"}, status=403, headers=headers,
            )

        # Rate limiting
        client_ip = request.remote or "unknown"
        if _is_rate_limited(client_ip):
            logger.warning("Rate limit exceeded: %s", client_ip)
            return web.json_response(
                {"error": "Too many requests"}, status=429, headers=headers,
            )

        try:
            data = await request.json()
            name = _sanitize(data.get("name", ""), 100) or "Не указано"
            phone = _sanitize(data.get("phone", ""), 30) or "Не указано"

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
                {"status": "ok"}, headers=headers,
            )

        except Exception as e:
            logger.error("Ошибка обработки lead-заявки: %s", e)
            return web.json_response(
                {"error": "Internal error"}, status=500, headers=headers,
            )

    async def handle_consultation(request: web.Request) -> web.Response:
        """
        Приём заявки с формы "Начни сегодня" (консультация).
        Ожидает JSON: { "name": "Имя", "phone": "+7...",
                        "direction": "personal", "goal": "Похудеть" }
        """
        origin = _get_origin(request)
        headers = _cors_headers(origin)

        # Проверяем Origin
        if origin and origin not in ALLOWED_ORIGINS:
            return web.json_response(
                {"error": "Forbidden"}, status=403, headers=headers,
            )

        # Rate limiting
        client_ip = request.remote or "unknown"
        if _is_rate_limited(client_ip):
            logger.warning("Rate limit exceeded: %s", client_ip)
            return web.json_response(
                {"error": "Too many requests"}, status=429, headers=headers,
            )

        try:
            data = await request.json()
            name = _sanitize(data.get("name", ""), 100) or "Не указано"
            phone = _sanitize(data.get("phone", ""), 30) or "Не указано"
            direction = _sanitize(data.get("direction", ""), 50) or "Не выбрано"
            goal = _sanitize(data.get("goal", ""), 300) or "Не указана"

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
                {"status": "ok"}, headers=headers,
            )

        except Exception as e:
            logger.error("Ошибка обработки consultation-заявки: %s", e)
            return web.json_response(
                {"error": "Internal error"}, status=500, headers=headers,
            )

    async def handle_options(request: web.Request) -> web.Response:
        """
        Обработка preflight-запросов (CORS).
        Браузер отправляет OPTIONS перед POST, чтобы проверить разрешения.
        """
        origin = _get_origin(request)
        return web.Response(status=200, headers=_cors_headers(origin))

    # Регистрируем маршруты
    app.router.add_post("/api/lead", handle_lead)
    app.router.add_post("/api/consultation", handle_consultation)
    # CORS preflight
    app.router.add_options("/api/lead", handle_options)
    app.router.add_options("/api/consultation", handle_options)

    async def handle_waitlist(request: web.Request) -> web.Response:
        """
        Приём заявки в лист ожидания (когда набор на онлайн-программы закрыт).
        Ожидает JSON: { "name": "Имя", "phone": "+7..." }
        """
        origin = _get_origin(request)
        headers = _cors_headers(origin)

        if origin and origin not in ALLOWED_ORIGINS:
            return web.json_response(
                {"error": "Forbidden"}, status=403, headers=headers,
            )

        client_ip = request.remote or "unknown"
        if _is_rate_limited(client_ip):
            return web.json_response(
                {"error": "Too many requests"}, status=429, headers=headers,
            )

        try:
            data = await request.json()
            name = _sanitize(data.get("name", ""), 100) or "Не указано"
            phone = _sanitize(data.get("phone", ""), 30) or "Не указано"

            text = (
                "📋 <b>Новая заявка в лист ожидания!</b>\n\n"
                f"<b>Имя:</b> {_escape(name)}\n"
                f"<b>Телефон/Telegram:</b> {_escape(phone)}\n\n"
                "Клиент хочет записаться на онлайн-программу, "
                "когда откроется следующий набор."
            )
            await bot.send_message(
                chat_id=config.admin_chat_id,
                text=text,
                parse_mode="HTML",
            )

            await save_lead(source="waitlist", name=name, phone=phone)
            logger.info("Waitlist заявка от %s отправлена тренеру", name)
            return web.json_response(
                {"status": "ok"}, headers=headers,
            )

        except Exception as e:
            logger.error("Ошибка обработки waitlist-заявки: %s", e)
            return web.json_response(
                {"error": "Internal error"}, status=500, headers=headers,
            )

    app.router.add_post("/api/waitlist", handle_waitlist)
    app.router.add_options("/api/waitlist", handle_options)


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
