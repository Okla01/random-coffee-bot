"""
Бизнес-логика блокировки и разблокировки пользователей.

Содержит функции для:
- блокировки пользователей
- разблокировки пользователей
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import User, AdminLog, Match
from app.services.const import (
    USER_STATUS_BLOCKED,
    USER_STATUS_NEW,
    USER_STATUS_ACTIVE,
    USER_STATUS_NOT_ACTIVE,
)
from app.services.matching.constants import (
    MATCH_ACTIVE_STATUSES,
    MATCH_STATUS_USER_A_BLOCKED,
    MATCH_STATUS_USER_B_BLOCKED,
)
from app.services.profile.utils import is_profile_complete


async def block_user(session: AsyncSession, admin_tg_id: int, user: User) -> list[Match]:
    """
    Блокирует пользователя, обновляет активные мэтчи и логирует действие.

    При блокировке пользователя все его активные мэтчи переводятся в статус
    user_a_blocked или user_b_blocked в зависимости от роли пользователя в мэтче.

    Args:
        session (AsyncSession): сессия БД.
        admin_tg_id (int): Telegram ID администратора, выполняющего действие.
        user (User): объект пользователя для блокировки.

    Returns:
        list[Match]: список обновлённых активных мэтчей (для последующей отправки уведомлений).
    """
    user.status = USER_STATUS_BLOCKED
    
    # Обновляем все активные мэтчи заблокированного пользователя
    active_matches_stmt = select(Match).where(
        or_(Match.user_a_id == user.id, Match.user_b_id == user.id),
        Match.status.in_(MATCH_ACTIVE_STATUSES)
    )
    result = await session.execute(active_matches_stmt)
    updated_matches = []
    
    for match in result.scalars():
        # Загружаем связанных пользователей для доступа к ним
        await session.refresh(match, ["user_a", "user_b"])
        
        if match.user_a_id == user.id:
            match.status = MATCH_STATUS_USER_A_BLOCKED
        else:
            match.status = MATCH_STATUS_USER_B_BLOCKED
        updated_matches.append(match)
    
    session.add(
        AdminLog(
            admin_id=admin_tg_id,
            action="block",
            payload={"user_id": user.id},
        )
    )
    await session.commit()
    
    return updated_matches


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



