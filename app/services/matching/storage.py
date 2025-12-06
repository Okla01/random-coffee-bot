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


async def set_match_feedback(
    session: AsyncSession,
    match: Match,
    user: User,
    feedback: str,
) -> bool:
    """
    Устанавливает обратную связь от пользователя и проверяет, нужно ли переводить матч в completed.

    Args:
        session (AsyncSession): активная сессия БД.
        match (Match): объект матча.
        user (User): пользователь, который дал обратную связь.
        feedback (str): тип обратной связи ("positive" или "complaint").

    Returns:
        bool: True если пользователь является участником матча и обратная связь установлена,
            False если пользователь не участвует в матче.
    """
    if user.id == match.user_a_id:
        match.user_a_feedback = feedback
    elif user.id == match.user_b_id:
        match.user_b_feedback = feedback
    else:
        return False
    await session.flush()
    return True


async def check_and_complete_match(
    session: AsyncSession,
    match: Match,
) -> bool:
    """
    Проверяет, дали ли оба пользователя обратную связь, и переводит матч в completed если да.

    Args:
        session (AsyncSession): активная сессия БД.
        match (Match): объект матча.

    Returns:
        bool: True если матч был переведён в completed, False если ещё не все дали обратную связь.
    """
    from app.services.matching.constants import MATCH_STATUS_COMPLETED
    
    # Проверяем, что оба пользователя дали обратную связь
    if match.user_a_feedback and match.user_b_feedback:
        match.status = MATCH_STATUS_COMPLETED
        await cleanup_inactive_match(session, match)
        await session.flush()
        return True
    return False


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
