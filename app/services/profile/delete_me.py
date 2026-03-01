"""
Бизнес-логика удаления профиля пользователя.

Содержит функции для полного удаления пользователя из базы данных.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import User, Match
from app.services.matching.constants import (
    MATCH_ACTIVE_STATUSES,
    MATCH_STATUS_SKIPPED,
    MATCH_USER_RESPONSE_SKIP,
)
from app.services.matching.messages import notify_match_user_deleted
from app.services.matching.storage import cleanup_inactive_match

logger = logging.getLogger(__name__)


async def delete_user_by_telegram_id(
    session: AsyncSession,
    telegram_id: int,
    bot=None,
) -> None:
    """
    Полностью удаляет пользователя из базы данных по telegram_id.

    Перед удалением завершает все активные мэтчи пользователя со статусом "skipped"
    и уведомляет партнёров. Затем удаляет пользователя и все связанные записи
    (благодаря каскадным удалениям в relationships модели User: otps, attempts, roles и т.д.).

    Args:
        session (AsyncSession): сессия БД.
        telegram_id (int): Telegram ID пользователя для удаления.
        bot: экземпляр бота для отправки уведомлений партнёрам (опционально).

    Returns:
        None: ничего не возвращает.
    """
    # Находим пользователя
    user = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    
    if not user:
        logger.warning("Пользователь с telegram_id %s не найден для удаления", telegram_id)
        return

    # Находим все активные мэтчи пользователя
    active_matches_stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(
            or_(Match.user_a_id == user.id, Match.user_b_id == user.id),
            Match.status.in_(MATCH_ACTIVE_STATUSES),
        )
    )
    result = await session.execute(active_matches_stmt)
    active_matches = list(result.scalars().all())

    # Завершаем все активные мэтчи со статусом "skipped" и уведомляем партнёров
    for match in active_matches:
        match.status = MATCH_STATUS_SKIPPED
        match.last_reminder_at = None
        # Устанавливаем skip для удаляемого пользователя
        if match.user_a_id == user.id:
            match.user_a_response = MATCH_USER_RESPONSE_SKIP
        else:
            match.user_b_response = MATCH_USER_RESPONSE_SKIP
        # Очищаем данные неактивного мэтча
        await cleanup_inactive_match(session, match)
        
        # Уведомляем партнёра, если бот доступен
        if bot:
            try:
                await notify_match_user_deleted(bot, match, user)
            except Exception as e:
                logger.warning(
                    "Не удалось уведомить партнёра об удалении пользователя (мэтч %s): %s",
                    match.id,
                    e,
                )

    await session.flush()

    # Удаляем пользователя напрямую по telegram_id через SQL DELETE
    # Каскадные удаления сработают автоматически благодаря ondelete="SET NULL" в foreign keys
    # (мэтчи не удалятся, user_a_id/user_b_id станут NULL)
    stmt = delete(User).where(User.telegram_id == telegram_id)
    await session.execute(stmt)
    await session.commit()
