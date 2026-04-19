"""
Работа с ЮKassa API — создание платежей и проверка статуса.

ЮKassa SDK синхронный, поэтому все вызовы оборачиваем в asyncio.to_thread(),
чтобы не блокировать бота.
"""

import asyncio
import logging
import uuid

from yookassa import Configuration, Payment

logger = logging.getLogger(__name__)


def configure(shop_id: str, secret_key: str) -> None:
    """Настройка ЮKassa SDK. Вызывается один раз при запуске бота."""
    Configuration.account_id = shop_id
    Configuration.secret_key = secret_key
    logger.info("ЮKassa настроена (shop_id=%s)", shop_id)


async def create_payment(
    amount: int,
    tariff_id: str,
    tariff_name: str,
    user_id: int,
    username: str,
    full_name: str,
    bot_username: str,
    email: str,
) -> dict:
    """
    Создаёт платёж в ЮKassa.

    Формирует чек (54-ФЗ) для самозанятого:
    - vat_code=1 — без НДС
    - payment_mode=full_payment, payment_subject=service
    - ЮKassa регистрирует чек в «Мой налог» через интеграцию с ФНС
      (настройка в ЛК ЮKassa → Самозанятость).

    Возвращает:
        {"payment_id": "...", "confirmation_url": "..."}
    """
    description = f"Онлайн-программа {tariff_name} — 30 дней"

    def _create():
        return Payment.create(
            {
                "amount": {"value": f"{amount}.00", "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": f"https://t.me/{bot_username}",
                },
                "capture": True,
                "description": description,
                "metadata": {
                    "user_id": str(user_id),
                    "tariff": tariff_id,
                    "username": username,
                    "full_name": full_name,
                },
                "receipt": {
                    "customer": {"email": email},
                    "items": [
                        {
                            "description": description,
                            "quantity": "1",
                            "amount": {
                                "value": f"{amount}.00",
                                "currency": "RUB",
                            },
                            "vat_code": 1,
                            "payment_mode": "full_payment",
                            "payment_subject": "service",
                        }
                    ],
                },
            },
            uuid.uuid4(),  # Ключ идемпотентности — защита от дублей
        )

    payment = await asyncio.to_thread(_create)

    logger.info(
        "Платёж создан: id=%s, tariff=%s, user=%s",
        payment.id,
        tariff_id,
        user_id,
    )

    return {
        "payment_id": payment.id,
        "confirmation_url": payment.confirmation.confirmation_url,
    }


async def check_payment(payment_id: str) -> dict:
    """
    Проверяет статус платежа в ЮKassa.

    Возвращает:
        {"payment_id": "...", "status": "succeeded|pending|canceled", "metadata": {...}}
    """

    def _find():
        return Payment.find_one(payment_id)

    payment = await asyncio.to_thread(_find)

    return {
        "payment_id": payment.id,
        "status": payment.status,
        "metadata": dict(payment.metadata) if payment.metadata else {},
    }
