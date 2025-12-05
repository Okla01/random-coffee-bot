"""
Бизнес-логика блокировки и разблокировки пользователей.

Содержит функции для:
- блокировки пользователей
- разблокировки пользователей
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import User, AdminLog
from app.services.const import (
    USER_STATUS_BLOCKED,
    USER_STATUS_NEW,
    USER_STATUS_ACTIVE,
    USER_STATUS_NOT_ACTIVE,
)
from app.services.profile.utils import is_profile_complete


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


async def unblock_user(
    session: AsyncSession,
    admin_tg_id: int,
    user: User,
    reset_stage: bool = False,
) -> None:
    """
    Разблокирует пользователя и логирует действие.

    Проверяет заполненность анкеты и устанавливает статус "Активный" или "Не активен"
    в зависимости от заполненности. Если reset_stage=True, сбрасывает stage на
    verifying_email и счётчики попыток (для случаев блокировки при неверных email/OTP).

    Args:
        session (AsyncSession): сессия БД.
        admin_tg_id (int): Telegram ID администратора, выполняющего действие.
        user (User): объект пользователя для разблокировки.
        reset_stage (bool): если True, сбрасывает stage на verifying_email и счётчики.

    Returns:
        None: ничего не возвращает.
    """
    if reset_stage:
        # Для случаев блокировки при неверных email/OTP - сбрасываем всё
        user.status = USER_STATUS_NEW
        user.stage = "verifying_email"
        user.email_attempts = 0
        user.otp_attempts = 0
    else:
        # Для обычной разблокировки - проверяем заполненность анкеты
        if is_profile_complete(user):
            user.status = USER_STATUS_ACTIVE
        else:
            user.status = USER_STATUS_NOT_ACTIVE
        # НЕ сбрасываем stage, email_attempts и otp_attempts

    session.add(
        AdminLog(
            admin_id=admin_tg_id,
            action="unblock",
            payload={"user_id": user.id, "reset_stage": reset_stage},
        )
    )
    await session.commit()
