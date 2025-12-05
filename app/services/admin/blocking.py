"""
Бизнес-логика блокировки и разблокировки пользователей.

Содержит функции для:
- блокировки пользователей
- разблокировки пользователей
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import User, AdminLog
from app.services.const import USER_STATUS_BLOCKED, USER_STATUS_NEW


async def block_user(session: AsyncSession, admin_tg_id: int, user: User) -> None:
    """
    Блокирует пользователя и логирует действие.

    Args:
        session (AsyncSession): сессия БД.
        admin_tg_id (int): Telegram ID администратора, выполняющего действие.
        user (User): объект пользователя для блокировки.

    Returns:
        None: ничего не возвращает.
    """
    user.status = USER_STATUS_BLOCKED
    session.add(
        AdminLog(
            admin_id=admin_tg_id,
            action="block",
            payload={"user_id": user.id},
        )
    )
    await session.commit()


async def unblock_user(session: AsyncSession, admin_tg_id: int, user: User) -> None:
    """
    Разблокирует пользователя, сбрасывает счётчики и логирует действие.

    Args:
        session (AsyncSession): сессия БД.
        admin_tg_id (int): Telegram ID администратора, выполняющего действие.
        user (User): объект пользователя для разблокировки.

    Returns:
        None: ничего не возвращает.
    """
    user.status = USER_STATUS_NEW
    user.stage = "verifying_email"
    user.email_attempts = 0
    user.otp_attempts = 0

    session.add(
        AdminLog(
            admin_id=admin_tg_id,
            action="unblock",
            payload={"user_id": user.id},
        )
    )
    await session.commit()
