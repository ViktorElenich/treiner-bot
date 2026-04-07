"""
Inline-кнопки для сообщений бота.
Это кнопки, которые появляются прямо под сообщением.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню после /start."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💪 Онлайн-программы",
            callback_data="show_tariffs",
        )],
        [InlineKeyboardButton(
            text="📋 Моя подписка",
            callback_data="my_subscription",
        )],
        [InlineKeyboardButton(
            text="📋 Записаться на консультацию",
            callback_data="consultation",
        )],
        [InlineKeyboardButton(
            text="ℹ️ О тренере",
            callback_data="about",
        )],
    ])


def tariffs_keyboard() -> InlineKeyboardMarkup:
    """Список тарифов онлайн-программ."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🟢 СТАРТ — 2 000 ₽/мес",
            callback_data="tariff_start",
        )],
        [InlineKeyboardButton(
            text="🔥 ПРОГРЕСС — 3 500 ₽/мес (хит)",
            callback_data="tariff_progress",
        )],
        [InlineKeyboardButton(
            text="🏆 РЕЗУЛЬТАТ — 6 000 ₽/мес",
            callback_data="tariff_result",
        )],
        [InlineKeyboardButton(
            text="« Назад",
            callback_data="back_to_menu",
        )],
    ])


def tariff_detail_keyboard(tariff_id: str) -> InlineKeyboardMarkup:
    """Кнопки после описания тарифа: оплатить или назад."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💳 Оплатить",
            callback_data=f"pay_{tariff_id}",
        )],
        [InlineKeyboardButton(
            text="« К тарифам",
            callback_data="show_tariffs",
        )],
    ])


def payment_keyboard(payment_url: str, payment_id: str) -> InlineKeyboardMarkup:
    """Кнопки после создания платежа: ссылка на оплату + проверка."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💳 Перейти к оплате",
            url=payment_url,
        )],
        [InlineKeyboardButton(
            text="✅ Я оплатил — проверить",
            callback_data=f"check_{payment_id}",
        )],
        [InlineKeyboardButton(
            text="« Отмена",
            callback_data="show_tariffs",
        )],
    ])


def after_payment_keyboard() -> InlineKeyboardMarkup:
    """Кнопки после успешной оплаты."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 Моя подписка",
            callback_data="my_subscription",
        )],
        [InlineKeyboardButton(
            text="« В главное меню",
            callback_data="back_to_menu",
        )],
    ])


def renew_subscription_keyboard(tariff_id: str) -> InlineKeyboardMarkup:
    """Кнопка продления подписки (в напоминаниях об истечении)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔄 Продлить подписку",
            callback_data=f"pay_{tariff_id}",
        )],
        [InlineKeyboardButton(
            text="« В главное меню",
            callback_data="back_to_menu",
        )],
    ])


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Кнопка "Назад в меню"."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="« В главное меню",
            callback_data="back_to_menu",
        )],
    ])
