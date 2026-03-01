"""
Утилиты для отправки критических уведомлений разработчику.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.services.core.config import Settings

logger = logging.getLogger(__name__)


async def send_critical_alert(
    bot: Bot,
    message: str,
) -> None:
    """
    Отправляет критическое уведомление разработчику.

    Args:
        bot: Экземпляр бота.
        message: Текст сообщения об ошибке.
    """
    settings = Settings.load()
    
    if not settings.developer_telegram_id:
        logger.warning("DEVELOPER_TELEGRAM_ID не задан, невозможно отправить критическое уведомление")
        return

    try:
        text = (
            "🚨 КРИТИЧЕСКАЯ ОШИБКА МЭТЧИНГА\n\n"
            f"{message}"
        )
        await bot.send_message(
            chat_id=settings.developer_telegram_id,
            text=text,
            parse_mode="HTML",
        )
        logger.info("Критическое уведомление отправлено разработчику")
    except TelegramAPIError as e:
        logger.error("Не удалось отправить критическое уведомление разработчику: %s", e)
    except Exception as e:
        logger.exception("Неожиданная ошибка при отправке критического уведомления: %s", e)
