"""
Бизнес-логика удаления профиля пользователя.

Содержит функции для полного удаления пользователя из базы данных.
"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import User


async def delete_user_by_telegram_id(
    session: AsyncSession,
    telegram_id: int,
) -> None:
    """
    Полностью удаляет пользователя из базы данных по telegram_id.

    Удаляет пользователя и все связанные записи (благодаря каскадным удалениям
    в relationships модели User: otps, attempts, roles и т.д.).

    Args:
        session (AsyncSession): сессия БД.
        telegram_id (int): Telegram ID пользователя для удаления.

    Returns:
        None: ничего не возвращает.
    """
    # Удаляем пользователя напрямую по telegram_id через SQL DELETE
    # Каскадные удаления сработают автоматически благодаря ondelete="CASCADE" в foreign keys
    stmt = delete(User).where(User.telegram_id == telegram_id)
    await session.execute(stmt)
    await session.commit()

