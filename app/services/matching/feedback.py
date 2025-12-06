"""
Система отправки обратной связи пользователям по расписанию.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import Match
from app.services.matching.constants import MATCH_STATUS_MATCHED
from app.keyboards.kb_matching import kb_meeting_feedback

logger = logging.getLogger(__name__)


async def send_feedback_to_users(
    session: AsyncSession,
    bot: Bot,
) -> int:
    """
    Отправляет запрос обратной связи всем пользователям с активными встречами (matched).

    Args:
        session: активная сессия БД.
        bot: экземпляр бота для отправки сообщений.

    Returns:
        int: количество пользователей, которым отправлена обратная связь.
    """
    stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(
            Match.status == MATCH_STATUS_MATCHED,
        )
    )
    result = await session.execute(stmt)
    matches = list(result.scalars().all())
    
    if not matches:
        return 0

    sent_count = 0
    for match in matches:
        text = (
            "Оцени как прошла твоя встреча!\nПосле оценки ты автоматически участвуешь в следующих раундах!"
        )
        markup = kb_meeting_feedback(match.id)

        for user in (match.user_a, match.user_b):
            if not user or not user.telegram_id:
                continue
            try:
                await bot.send_message(
                    user.telegram_id,
                    text,
                    reply_markup=markup,
                )
                sent_count += 1
            except Exception as e:
                logger.warning(
                    "Failed to send feedback request to user %d: %s",
                    user.telegram_id,
                    e,
                )

    return sent_count

