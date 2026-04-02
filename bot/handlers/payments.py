"""
Обработка оплаты тарифов через ЮKassa.

Поток:
1. Клиент нажимает "Оплатить" → бот создаёт платёж в ЮKassa
2. Клиент оплачивает на странице ЮKassa
3. Клиент возвращается в бот, нажимает "Проверить"
4. Бот проверяет статус → сохраняет подписку → выдаёт ссылку на канал
"""

import logging
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery

from bot.config import load_config
from bot.handlers.start import TARIFFS
from bot.services.yukassa import create_payment, check_payment
from bot.services.channel import create_invite_link, get_group_id_for_tariff
from bot.database import get_active_subscription, save_subscription
from bot.keyboards.inline import (
    back_to_menu_keyboard,
    payment_keyboard,
    after_payment_keyboard,
)

router = Router()
logger = logging.getLogger(__name__)


# ── Кнопка "Оплатить" ───────────────────────────────────────────

@router.callback_query(F.data.startswith("pay_"))
async def handle_pay(callback: CallbackQuery, bot: Bot):
    """Клиент нажал "Оплатить" на тарифе."""
    tariff_id = callback.data.replace("pay_", "")
    tariff = TARIFFS.get(tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    user = callback.from_user

    # Проверяем: может, уже есть активная подписка?
    existing = await get_active_subscription(user.id)
    if existing:
        expires = existing["expires_at"][:10]
        await callback.message.edit_text(
            f"✅ <b>У тебя уже есть активная подписка</b>\n\n"
            f"Тариф: <b>{TARIFFS.get(existing['tariff'], {}).get('name', existing['tariff'])}</b>\n"
            f"Действует до: {expires}\n\n"
            "Дождись окончания текущей подписки или напиши "
            "тренеру для смены тарифа: @ViktorElenich",
            reply_markup=back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    # Создаём платёж в ЮKassa
    try:
        me = await bot.get_me()
        result = await create_payment(
            amount=tariff["price"],
            tariff_id=tariff_id,
            tariff_name=tariff["name"],
            user_id=user.id,
            username=user.username or "",
            full_name=user.full_name or "",
            bot_username=me.username,
        )

        await callback.message.edit_text(
            f"💳 <b>Оплата тарифа {tariff['name']}</b>\n\n"
            f"Сумма: <b>{tariff['price']} ₽</b>\n"
            f"Период: 30 дней\n\n"
            "Нажми кнопку ниже для перехода к оплате.\n"
            "После оплаты вернись сюда и нажми «Проверить оплату».",
            reply_markup=payment_keyboard(
                result["confirmation_url"], result["payment_id"]
            ),
        )

    except Exception as e:
        logger.error("Ошибка создания платежа: %s", e)
        await callback.message.edit_text(
            "❌ <b>Ошибка создания платежа</b>\n\n"
            "Попробуй позже или напиши тренеру:\n"
            "👉 @ViktorElenich",
            reply_markup=back_to_menu_keyboard(),
        )

    await callback.answer()


# ── Кнопка "Я оплатил — проверить" ──────────────────────────────

@router.callback_query(F.data.startswith("check_"))
async def handle_check_payment(callback: CallbackQuery, bot: Bot):
    """Клиент нажал "Проверить оплату" после оплаты на сайте ЮKassa."""
    payment_id = callback.data.replace("check_", "")

    try:
        result = await check_payment(payment_id)
    except Exception as e:
        logger.error("Ошибка проверки платежа: %s", e)
        await callback.answer(
            "Ошибка проверки. Попробуй ещё раз через минуту.",
            show_alert=True,
        )
        return

    if result["status"] == "succeeded":
        # Оплата прошла!
        await process_successful_payment(
            bot=bot,
            callback=callback,
            payment_id=payment_id,
            user_id=callback.from_user.id,
            username=callback.from_user.username or "",
            full_name=callback.from_user.full_name or "",
            tariff_id=result["metadata"].get("tariff", ""),
        )

    elif result["status"] == "pending":
        await callback.answer(
            "⏳ Оплата ещё не прошла. Заверши оплату и нажми снова.",
            show_alert=True,
        )

    elif result["status"] == "canceled":
        await callback.message.edit_text(
            "❌ <b>Платёж отменён</b>\n\n"
            "Попробуй оплатить заново.",
            reply_markup=back_to_menu_keyboard(),
        )
        await callback.answer()

    else:
        await callback.answer(
            f"Статус платежа: {result['status']}. Попробуй позже.",
            show_alert=True,
        )


# ── Кнопка "Моя подписка" ───────────────────────────────────────

@router.callback_query(F.data == "my_subscription")
async def handle_my_subscription(callback: CallbackQuery):
    """Показать текущую подписку клиента."""
    sub = await get_active_subscription(callback.from_user.id)

    if sub:
        tariff = TARIFFS.get(sub["tariff"], {})
        name = tariff.get("name", sub["tariff"])
        expires = sub["expires_at"][:10]
        await callback.message.edit_text(
            f"📋 <b>Твоя подписка</b>\n\n"
            f"Тариф: <b>{name}</b>\n"
            f"Активна до: {expires}",
            reply_markup=back_to_menu_keyboard(),
        )
    else:
        await callback.message.edit_text(
            "📋 <b>У тебя нет активной подписки</b>\n\n"
            "Выбери тариф и оплати для доступа к каналу.",
            reply_markup=back_to_menu_keyboard(),
        )

    await callback.answer()


# ── Общая логика после успешной оплаты ───────────────────────────

async def process_successful_payment(
    bot: Bot,
    callback: Optional[CallbackQuery],
    payment_id: str,
    user_id: int,
    username: str,
    full_name: str,
    tariff_id: str,
) -> None:
    """
    Вызывается после подтверждения оплаты (и из кнопки "Проверить",
    и из webhook ЮKassa).

    1. Проверяет дубликат (идемпотентность)
    2. Сохраняет подписку в БД
    3. Создаёт invite-ссылку на канал
    4. Отправляет ссылку клиенту
    5. Уведомляет тренера
    """
    config = load_config()
    tariff = TARIFFS.get(tariff_id)

    if not tariff:
        logger.error("Неизвестный тариф %s для платежа %s", tariff_id, payment_id)
        return

    # Защита от дублей — если payment_id уже в БД, пропускаем
    existing = await get_active_subscription(user_id)
    if existing and existing.get("payment_id") == payment_id:
        logger.info("Платёж %s уже обработан, пропускаю", payment_id)
        return

    # Сохраняем подписку
    await save_subscription(
        user_id=user_id,
        username=username,
        full_name=full_name,
        tariff=tariff_id,
        price=tariff["price"],
        payment_id=payment_id,
    )

    # Создаём invite-ссылки
    user_name = full_name or username or str(user_id)

    # Ссылка на общий чат
    general_url = None
    if config.group_general_id:
        general_url = await create_invite_link(
            bot=bot,
            channel_id=config.group_general_id,
            user_name=user_name,
        )

    # Ссылка на группу тарифа
    group_id = get_group_id_for_tariff(tariff_id, config)
    group_url = None
    if group_id:
        group_url = await create_invite_link(
            bot=bot,
            channel_id=group_id,
            user_name=user_name,
        )

    # Формируем сообщение для клиента
    success_text = (
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"Тариф: <b>{tariff['name']}</b>\n"
        f"Сумма: {tariff['price']} ₽\n"
        f"Период: 30 дней\n\n"
    )

    if general_url or group_url:
        success_text += "🔗 <b>Твои ссылки:</b>\n\n"
        if general_url:
            success_text += f"💬 Общий чат: {general_url}\n"
        if group_url:
            success_text += f"🏋️ Чат тарифа {tariff['name']}: {group_url}\n"
        success_text += "\n⚠️ Ссылки одноразовые, действуют 24 часа."
    else:
        success_text += (
            "⚠️ Не удалось создать ссылки.\n"
            "Напиши тренеру @ViktorElenich — он добавит вручную."
        )

    # Отправляем клиенту
    if callback:
        # Вызвано из кнопки "Проверить"
        await callback.message.edit_text(
            success_text,
            reply_markup=after_payment_keyboard(),
        )
    else:
        # Вызвано из webhook ЮKassa
        await bot.send_message(
            chat_id=user_id,
            text=success_text,
            reply_markup=after_payment_keyboard(),
        )

    # Уведомляем тренера
    admin_text = (
        f"💰 <b>Новая оплата!</b>\n\n"
        f"Клиент: {full_name}"
    )
    if username:
        admin_text += f" (@{username})"
    admin_text += (
        f"\nТариф: {tariff['name']}\n"
        f"Сумма: {tariff['price']} ₽\n"
        f"ID платежа: <code>{payment_id}</code>"
    )

    await bot.send_message(
        chat_id=config.admin_chat_id,
        text=admin_text,
    )

    logger.info(
        "Оплата обработана: user=%s, tariff=%s, payment=%s",
        user_id,
        tariff_id,
        payment_id,
    )
