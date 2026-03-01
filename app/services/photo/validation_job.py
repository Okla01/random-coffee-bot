"""
Периодическая задача для валидации всех фото пользователей.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import User
from app.services.const import USER_STATUS_ACTIVE
from app.services.photo.service import validate_and_refresh_photos
from app.services.core.config import Settings

logger = logging.getLogger(__name__)


async def validate_all_user_photos_periodically(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """
    Периодическая задача для валидации всех фото пользователей.
    
    Запускается каждый день в 3:00 МСК для предварительной валидации всех фото,
    чтобы избежать массовой валидации во время мэтчинга.
    
    Args:
        session_factory: Фабрика сессий БД.
        bot: Экземпляр бота.
    """
    logger.info("Задача валидации фото запущена планировщиком")
    
    settings = Settings.load()
    if not settings.photos_storage_chat_id:
        logger.warning("photos_storage_chat_id не задан, пропускаю валидацию фото")
        return
    
    try:
        async with session_factory() as session:
            # Загружаем всех активных пользователей с фото
            stmt = select(User).where(
                User.status == USER_STATUS_ACTIVE,
                User.photos_json.isnot(None),
            )
            result = await session.execute(stmt)
            users = list(result.scalars().all())
            
            if not users:
                logger.info("Нет пользователей с фото для валидации")
                return
            
            logger.info("Начинаю валидацию фото для %d пользователей", len(users))
            
            validated_count = 0
            failed_count = 0
            
            for user in users:
                try:
                    has_valid_photos = await validate_and_refresh_photos(
                        bot, user, settings, session
                    )
                    await session.flush()
                    if has_valid_photos:
                        validated_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    logger.exception(
                        "Ошибка валидации фото для user=%s: %s",
                        user.telegram_id, e
                    )
                    failed_count += 1
                    # Продолжаем для других пользователей
                    continue
            
            # Коммитим все изменения
            await session.commit()
            
            logger.info(
                "Валидация фото завершена. Успешно: %d, Ошибок: %d",
                validated_count, failed_count
            )
            
    except Exception as e:
        logger.exception("Задача валидации фото завершилась ошибкой: %s", e)
        raise
