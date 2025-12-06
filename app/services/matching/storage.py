"""
Утилиты доступа к данным матчей.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import Match, User
from app.database.utils import MOSCOW_TZ


async def get_match_with_relations(
    session: AsyncSession, match_id: int
) -> Match | None:
    """
    Загружает матч с предзагруженными связанными пользователями.

    Args:
        session (AsyncSession): активная сессия БД.
        match_id (int): ID матча.

    Returns:
        Match | None: объект матча с загруженными user_a и user_b, или None если не найден.
    """
    stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(Match.id == match_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def set_match_response(
    session: AsyncSession,
    match: Match,
    user: User,
    response: str,
) -> bool:
    """
    Обновляет поле ответа участника матча (user_a_response или user_b_response).

    Args:
        session (AsyncSession): активная сессия БД.
        match (Match): объект матча.
        user (User): пользователь, чей ответ обновляется.
        response (str): значение ответа (ready, skip, confirm, none).

    Returns:
        bool: True если пользователь является участником матча и ответ обновлён,
            False если пользователь не участвует в матче.
    """
    if user.id == match.user_a_id:
        match.user_a_response = response
    elif user.id == match.user_b_id:
        match.user_b_response = response
    else:
        return False
    await session.flush()
    return True


async def cleanup_inactive_match(
    session: AsyncSession,
    match: Match,
) -> None:
    """
    Очищает данные матча при переходе в неактивный статус.

    Обнуляет поля last_message_id_a и last_message_id_b.

    Args:
        session (AsyncSession): активная сессия БД.
        match (Match): объект матча.

    Returns:
        None: ничего не возвращает.
    """
    # Обнуляем ID последних сообщений
    match.last_message_id_a = None
    match.last_message_id_b = None
