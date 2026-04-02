"""
Управление доступом к закрытому каналу и группам тарифов.
Создание одноразовых invite-ссылок после оплаты.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot

logger = logging.getLogger(__name__)


async def create_invite_link(
    bot: Bot, channel_id: int, user_name: str
) -> Optional[str]:
    """
    Создаёт одноразовую ссылку на закрытый канал/группу.

    Ссылка:
    - Можно использовать 1 раз (member_limit=1)
    - Действует 24 часа

    Возвращает URL ссылки или None при ошибке.
    """
    try:
        link = await bot.create_chat_invite_link(
            chat_id=channel_id,
            name=f"Оплата: {user_name}",
            member_limit=1,
            expire_date=datetime.utcnow() + timedelta(hours=24),
        )
        logger.info("Invite-ссылка создана для %s (chat_id=%s)", user_name, channel_id)
        return link.invite_link

    except Exception as e:
        logger.error("Не удалось создать invite-ссылку (chat_id=%s): %s", channel_id, e)
        return None


def get_group_id_for_tariff(tariff_id: str, config) -> int:
    """Возвращает ID группы для тарифа."""
    mapping = {
        "start": config.group_start_id,
        "progress": config.group_progress_id,
        "result": config.group_result_id,
    }
    return mapping.get(tariff_id, 0)
