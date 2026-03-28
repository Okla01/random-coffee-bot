"""
Periodic task for soft validation of user photos.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import User
from app.services.core.config import Settings
from app.services.photo.service import validate_and_refresh_photos

logger = logging.getLogger(__name__)


async def validate_all_user_photos_periodically(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """Refresh photo file_id values for every user that still has photos_json."""
    logger.info("Задача валидации фото запущена планировщиком")

    settings = Settings.load()
    if not settings.photos_storage_chat_id:
        logger.warning("photos_storage_chat_id не задан, пропускаю валидацию фото")
        return

    try:
        async with session_factory() as session:
            stmt = select(User).where(User.photos_json.isnot(None))
            result = await session.execute(stmt)
            users = list(result.scalars().all())

            if not users:
                logger.info("Нет пользователей с фото для валидации")
                return

            logger.info("Начинаю валидацию фото для %d пользователей", len(users))

            processed_count = 0
            available_count = 0
            error_count = 0

            for user in users:
                try:
                    has_available_photos = await validate_and_refresh_photos(
                        bot,
                        user,
                        settings,
                        session,
                    )
                    await session.flush()
                    processed_count += 1
                    if has_available_photos:
                        available_count += 1
                except Exception as exc:
                    logger.exception(
                        "Ошибка валидации фото для user=%s: %s",
                        user.telegram_id,
                        exc,
                    )
                    error_count += 1
                    continue

            await session.commit()

            logger.info(
                "Валидация фото завершена. Обработано: %d, доступно фото: %d, ошибок: %d",
                processed_count,
                available_count,
                error_count,
            )

    except Exception as exc:
        logger.exception("Задача валидации фото завершилась ошибкой: %s", exc)
        raise
