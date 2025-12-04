"""
Периодические задачи домена матчинга.
"""

from __future__ import annotations

from datetime import timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import Match
from app.database.utils import now_msk
from app.services.matching.constants import (
    MATCH_STATUS_COMPLETED,
    MATCH_STATUS_EXPIRED_TIMEOUT,
    MATCH_STATUS_PENDING_RESPONSE,
    MATCH_STATUS_SCHEDULED,
    MATCH_STATUS_WAITING_CONFIRM,
    MATCH_STATUS_WAITING_SLOTS,
    MATCH_USER_RESPONSE_NONE,
)
from app.services.matching.messages import (
    notify_match_reminder,
    notify_match_timeout,
    notify_meeting_started,
)
from app.services.matching.settings import MatchingSettings


async def complete_due_meetings(
    session: AsyncSession,
    bot: Bot | None = None,
) -> int:
    """
    Переводит матчи со статусом scheduled в completed, если время встречи наступило.

    Args:
        session: активная сессия БД.
        bot: экземпляр бота для отправки уведомлений (опционально).

    Returns:
        int: количество обработанных матчей.
    """
    now = now_msk()
    stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(
            Match.status == MATCH_STATUS_SCHEDULED,
            Match.meeting_start_at <= now,
        )
    )
    result = await session.execute(stmt)
    matches = list(result.scalars().all())
    if not matches:
        return 0

    for match in matches:
        match.status = MATCH_STATUS_COMPLETED
        if match.user_a:
            match.user_a.last_match_at = now
        if match.user_b:
            match.user_b.last_match_at = now

    await session.commit()

    if bot:
        for match in matches:
            await notify_meeting_started(bot, match)

    return len(matches)


async def process_match_timeouts_and_reminders(
    session: AsyncSession,
    settings: MatchingSettings,
    bot: Bot | None = None,
) -> dict[str, int]:
    """
    Обрабатывает напоминания и таймауты для матчей в активных стадиях.

    Проверяет матчи со статусами pending_response, waiting_slots, waiting_confirm:
    - отправляет напоминания с интервалом reminder_interval_hours;
    - переводит в expired_timeout при превышении response_timeout_hours.

    Args:
        session (AsyncSession): активная сессия БД.
        settings (MatchingSettings): настройки матчинга (таймауты, интервалы напоминаний).
        bot (Bot | None): экземпляр бота для отправки уведомлений (опционально).

    Returns:
        dict[str, int]: статистика обработки вида {"reminded": X, "expired": Y}.
    """
    now = now_msk()
    reminder_delta = timedelta(hours=max(0, settings.reminder_interval_hours))
    timeout_delta = timedelta(hours=max(1, settings.response_timeout_hours))

    stats = {"reminded": 0, "expired": 0}
    stage_map = {
        MATCH_STATUS_PENDING_RESPONSE: "pending_response",
        MATCH_STATUS_WAITING_SLOTS: "waiting_slots",
        MATCH_STATUS_WAITING_CONFIRM: "waiting_confirm",
    }

    stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(Match.status.in_(stage_map.keys()))
    )
    result = await session.execute(stmt)
    matches = list(result.scalars().all())

    for match in matches:
        stage = stage_map[match.status]
        
        # Определяем момент начала стадии согласно ТЗ:
        # - для pending_response: created_at (момент нахождения пары)
        # - для waiting_slots и waiting_confirm: updated_at (момент перехода в стадию)
        if stage == "pending_response":
            stage_start = match.created_at
        else:
            stage_start = match.updated_at
        
        if not stage_start:
            stage_start = match.created_at or now

        elapsed = now - stage_start

        # Проверяем таймаут: если прошло больше response_timeout_hours
        if elapsed >= timeout_delta:
            match.status = MATCH_STATUS_EXPIRED_TIMEOUT
            match.user_a_response = MATCH_USER_RESPONSE_NONE
            match.user_b_response = MATCH_USER_RESPONSE_NONE
            match.last_reminder_at = None
            stats["expired"] += 1
            continue

        # Напоминания отправляются только если:
        # 1. Прошло k * reminder_interval_hours (k >= 1), но меньше response_timeout_hours
        # 2. С момента последнего напоминания прошло >= reminder_interval_hours
        if reminder_delta <= timedelta(0) or bot is None:
            continue

        # Проверяем, что не превышен таймаут (напоминания только до таймаута)
        if elapsed >= timeout_delta:
            continue

        # Напоминание отправляется если:
        # - прошло >= reminder_interval_hours с начала стадии
        # - и (еще не отправляли ИЛИ с последнего напоминания прошло >= reminder_interval_hours)
        if elapsed >= reminder_delta:
            since_last = (
                now - match.last_reminder_at if match.last_reminder_at else None
            )
            # Отправляем напоминание если:
            # - еще не отправляли (since_last is None)
            # - или с последнего напоминания прошло >= reminder_interval_hours
            if since_last is None or since_last >= reminder_delta:
                await notify_match_reminder(bot, match, stage)
                match.last_reminder_at = now
                stats["reminded"] += 1

    await session.commit()

    expired_matches = [m for m in matches if m.status == MATCH_STATUS_EXPIRED_TIMEOUT]
    if bot:
        for match in expired_matches:
            await notify_match_timeout(bot, match)

    return stats

