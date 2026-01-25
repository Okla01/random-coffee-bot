"""
Сервисный слой для работы с рассылками администратора.

Содержит функции для создания, планирования и отправки рассылок пользователям.
Включает rate limiting для предотвращения банов от Telegram.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import InputMediaPhoto
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Broadcast, User
from app.database.utils import now_msk


async def create_broadcast(
    session: AsyncSession,
    admin_id: int,
    message_text: Optional[str] = None,
    media_json: Optional[dict] = None,
    scheduled_at: Optional[datetime] = None,
) -> Broadcast:
    """
    Создаёт новую рассылку в базе данных.
    
    Args:
        session: Сессия базы данных
        admin_id: telegram_id администратора
        message_text: Текст сообщения
        media_json: JSON с медиа-файлами
        scheduled_at: Запланированное время отправки (None = отправить сейчас)
    
    Returns:
        Broadcast: Созданная рассылка
    """
    broadcast = Broadcast(
        admin_id=admin_id,
        message_text=message_text,
        media_json=media_json,
        scheduled_at=scheduled_at,
        status="scheduled" if scheduled_at else "draft",
        created_at=now_msk(),
    )
    session.add(broadcast)
    await session.commit()
    await session.refresh(broadcast)
    return broadcast


async def get_active_users(session: AsyncSession) -> list[User]:
    """
    Получает список всех активных пользователей для рассылки.
    
    Args:
        session: Сессия базы данных
    
    Returns:
        list[User]: Список активных пользователей
    """
    result = await session.execute(
        select(User).where(
            User.status == "active",
            User.profile_approved == True,  # noqa: E712
        )
    )
    return list(result.scalars().all())


async def send_broadcast(
    bot: Bot,
    session: AsyncSession,
    broadcast_id: int,
    rate_limit_delay: float = 0.05,  # 50ms между сообщениями = ~20 сообщений/сек
) -> tuple[int, int]:
    """
    Отправляет рассылку всем активным пользователям с rate limiting.
    
    Args:
        bot: Экземпляр бота
        session: Сессия базы данных
        broadcast_id: ID рассылки
        rate_limit_delay: Задержка между отправками в секундах
    
    Returns:
        tuple[int, int]: (количество успешных отправок, количество ошибок)
    """
    # Получаем рассылку
    result = await session.execute(
        select(Broadcast).where(Broadcast.id == broadcast_id)
    )
    broadcast = result.scalar_one_or_none()
    
    if not broadcast:
        raise ValueError(f"Broadcast {broadcast_id} not found")
    
    # Обновляем статус на "sending"
    broadcast.status = "sending"
    await session.commit()
    
    # Получаем всех активных пользователей
    users = await get_active_users(session)
    broadcast.total_users = len(users)
    await session.commit()
    
    sent_count = 0
    failed_count = 0
    
    for user in users:
        try:
            # Отправляем сообщение
            if broadcast.media_json:
                media_type = broadcast.media_json.get("type")
                
                # Проверяем, это медиа-группа или одиночное медиа
                if media_type == "media_group":
                    # Отправляем медиа-группу (альбом)
                    media_items = broadcast.media_json.get("items", [])
                    
                    if media_items:
                        # Формируем список InputMedia объектов (только фото)
                        media_group = []
                        for idx, item in enumerate(media_items):
                            item_type = item.get("type")
                            file_id = item.get("file_id")
                            
                            # Первый элемент получает caption с текстом рассылки
                            caption = broadcast.message_text if idx == 0 else None
                            
                            if item_type == "photo":
                                media_group.append(
                                    InputMediaPhoto(
                                        media=file_id,
                                        caption=caption,
                                        parse_mode="HTML" if caption else None,
                                    )
                                )
                        
                        # Отправляем медиа-группу
                        if media_group:
                            await bot.send_media_group(
                                chat_id=user.telegram_id,
                                media=media_group,
                            )
                else:
                    # Одиночное фото
                    file_id = broadcast.media_json.get("file_id")
                    
                    if media_type == "photo":
                        await bot.send_photo(
                            chat_id=user.telegram_id,
                            photo=file_id,
                            caption=broadcast.message_text,
                            parse_mode="HTML",
                        )
            else:
                # Только текст
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=broadcast.message_text or "Рассылка",
                    parse_mode="HTML",
                )
            
            sent_count += 1
            
        except (TelegramForbiddenError, TelegramBadRequest):
            # Пользователь заблокировал бота или удалил аккаунт
            failed_count += 1
        except Exception:
            # Другие ошибки
            failed_count += 1
        
        # Rate limiting: задержка между отправками
        await asyncio.sleep(rate_limit_delay)
        
        # Обновляем прогресс каждые 10 пользователей
        if (sent_count + failed_count) % 10 == 0:
            broadcast.sent_count = sent_count
            broadcast.failed_count = failed_count
            await session.commit()
    
    # Финальное обновление статистики
    broadcast.sent_count = sent_count
    broadcast.failed_count = failed_count
    broadcast.status = "completed"
    broadcast.sent_at = now_msk()
    await session.commit()
    
    return sent_count, failed_count


async def get_broadcast(session: AsyncSession, broadcast_id: int) -> Optional[Broadcast]:
    """
    Получает рассылку по ID.
    
    Args:
        session: Сессия базы данных
        broadcast_id: ID рассылки
    
    Returns:
        Optional[Broadcast]: Рассылка или None
    """
    result = await session.execute(
        select(Broadcast).where(Broadcast.id == broadcast_id)
    )
    return result.scalar_one_or_none()


async def delete_broadcast(session: AsyncSession, broadcast_id: int) -> bool:
    """
    Удаляет рассылку из базы данных.
    
    Args:
        session: Сессия базы данных
        broadcast_id: ID рассылки
    
    Returns:
        bool: True если удалена, False если не найдена
    """
    broadcast = await get_broadcast(session, broadcast_id)
    if not broadcast:
        return False
    
    await session.delete(broadcast)
    await session.commit()
    return True


async def get_scheduled_broadcasts(session: AsyncSession) -> list[Broadcast]:
    """
    Получает все запланированные рассылки, готовые к отправке.
    
    Args:
        session: Сессия базы данных
    
    Returns:
        list[Broadcast]: Список запланированных рассылок
    """
    now = now_msk()
    result = await session.execute(
        select(Broadcast).where(
            Broadcast.status == "scheduled",
            Broadcast.scheduled_at <= now,
        )
    )
    return list(result.scalars().all())
