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
from app.database.utils import ensure_aware_msk, now_msk
from app.services.matching.constants import (
    MATCH_ACTIVE_STATUSES,
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
from app.services.matching.settings import MatchingSettings, parse_time_to_hours
from app.services.matching.storage import cleanup_inactive_match


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
        # Очищаем данные неактивного матча
        await cleanup_inactive_match(session, match)

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
    - отправляет напоминания с интервалом reminder_interval_time;
    - переводит в expired_timeout при превышении response_timeout_time.

    Args:
        session (AsyncSession): активная сессия БД.
        settings (MatchingSettings): настройки матчинга (таймауты, интервалы напоминаний).
        bot (Bot | None): экземпляр бота для отправки уведомлений (опционально).

    Returns:
        dict[str, int]: статистика обработки вида {"reminded": X, "expired": Y}.
    """
    now = now_msk()
    # Конвертация из формата ЧЧ:ММ в часы для timedelta
    reminder_hours = parse_time_to_hours(settings.reminder_interval_time)
    timeout_hours = parse_time_to_hours(settings.response_timeout_time)
    reminder_delta = timedelta(hours=max(0, reminder_hours))
    timeout_delta = timedelta(hours=max(1, timeout_hours))

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
        
        # Приводим к aware формату для корректного вычитания
        stage_start = ensure_aware_msk(stage_start) or now

        elapsed = now - stage_start

        # Проверяем таймаут: если прошло больше response_timeout_time
        if elapsed >= timeout_delta:
            match.status = MATCH_STATUS_EXPIRED_TIMEOUT
            match.user_a_response = MATCH_USER_RESPONSE_NONE
            match.user_b_response = MATCH_USER_RESPONSE_NONE
            match.last_reminder_at = None
            # Очищаем данные неактивного матча
            await cleanup_inactive_match(session, match)
            stats["expired"] += 1
            continue

        # Напоминания отправляются только если:
        # 1. Прошло k * reminder_interval_time (k >= 1), но меньше response_timeout_time
        # 2. С момента последнего напоминания прошло >= reminder_interval_time
        if reminder_delta <= timedelta(0) or bot is None:
            continue

        # Проверяем, что не превышен таймаут (напоминания только до таймаута)
        if elapsed >= timeout_delta:
            continue

        # Напоминание отправляется если:
        # - прошло >= reminder_interval_time с начала стадии
        # - и (еще не отправляли ИЛИ с последнего напоминания прошло >= reminder_interval_time)
        if elapsed >= reminder_delta:
            since_last = None
            if match.last_reminder_at:
                last_reminder_aware = ensure_aware_msk(match.last_reminder_at)
                if last_reminder_aware:
                    since_last = now - last_reminder_aware
            # Отправляем напоминание если:
            # - еще не отправляли (since_last is None)
            # - или с последнего напоминания прошло >= reminder_interval_time
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


async def process_match_timeouts_only(
    session: AsyncSession,
    settings: MatchingSettings,
    bot: Bot | None = None,
) -> int:
    """
    Проверяет и обрабатывает только таймауты для матчей в активных стадиях.

    Проверяет матчи с активными статусами и переводит их в expired_timeout
    при превышении response_timeout_time.

    Args:
        session (AsyncSession): активная сессия БД.
        settings (MatchingSettings): настройки матчинга (таймауты).
        bot (Bot | None): экземпляр бота для отправки уведомлений (опционально).

    Returns:
        int: количество истёкших матчей.
    """
    now = now_msk()
    timeout_hours = parse_time_to_hours(settings.response_timeout_time)
    timeout_delta = timedelta(hours=max(1, timeout_hours))

    # Статусы, для которых проверяются таймауты (исключаем scheduled, там нет таймаутов)
    timeout_check_statuses = {
        MATCH_STATUS_PENDING_RESPONSE,
        MATCH_STATUS_WAITING_SLOTS,
        MATCH_STATUS_WAITING_CONFIRM,
    }

    stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(Match.status.in_(timeout_check_statuses))
    )
    result = await session.execute(stmt)
    matches = list(result.scalars().all())

    expired_count = 0
    stage_map = {
        MATCH_STATUS_PENDING_RESPONSE: "pending_response",
        MATCH_STATUS_WAITING_SLOTS: "waiting_slots",
        MATCH_STATUS_WAITING_CONFIRM: "waiting_confirm",
    }

    for match in matches:
        stage = stage_map.get(match.status)
        if not stage:
            continue
        
        # Определяем момент начала стадии
        if stage == "pending_response":
            stage_start = match.created_at
        else:
            stage_start = match.updated_at
        
        if not stage_start:
            stage_start = match.created_at or now
        
        # Приводим к aware формату для корректного вычитания
        stage_start = ensure_aware_msk(stage_start) or now

        elapsed = now - stage_start

        # Проверяем таймаут
        if elapsed >= timeout_delta:
            match.status = MATCH_STATUS_EXPIRED_TIMEOUT
            match.user_a_response = MATCH_USER_RESPONSE_NONE
            match.user_b_response = MATCH_USER_RESPONSE_NONE
            match.last_reminder_at = None
            # Очищаем данные неактивного матча
            await cleanup_inactive_match(session, match)
            expired_count += 1

    await session.commit()

    expired_matches = [m for m in matches if m.status == MATCH_STATUS_EXPIRED_TIMEOUT]
    if bot:
        for match in expired_matches:
            await notify_match_timeout(bot, match)

    return expired_count


async def process_match_reminders_only(
    session: AsyncSession,
    settings: MatchingSettings,
    bot: Bot | None = None,
) -> int:
    """
    Проверяет и отправляет только напоминания для матчей в активных стадиях.

    Проверяет матчи с активными статусами и отправляет напоминания,
    если прошло достаточно времени с момента последнего напоминания.

    Args:
        session (AsyncSession): активная сессия БД.
        settings (MatchingSettings): настройки матчинга (интервалы напоминаний).
        bot (Bot | None): экземпляр бота для отправки уведомлений (опционально).

    Returns:
        int: количество отправленных напоминаний.
    """
    if bot is None:
        return 0

    now = now_msk()
    reminder_hours = parse_time_to_hours(settings.reminder_interval_time)
    timeout_hours = parse_time_to_hours(settings.response_timeout_time)
    reminder_delta = timedelta(hours=max(0, reminder_hours))
    timeout_delta = timedelta(hours=max(1, timeout_hours))

    if reminder_delta <= timedelta(0):
        return 0

    # Статусы, для которых отправляются напоминания (исключаем scheduled, там нет напоминаний)
    reminder_statuses = {
        MATCH_STATUS_PENDING_RESPONSE,
        MATCH_STATUS_WAITING_SLOTS,
        MATCH_STATUS_WAITING_CONFIRM,
    }

    stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(Match.status.in_(reminder_statuses))
    )
    result = await session.execute(stmt)
    matches = list(result.scalars().all())

    reminded_count = 0
    stage_map = {
        MATCH_STATUS_PENDING_RESPONSE: "pending_response",
        MATCH_STATUS_WAITING_SLOTS: "waiting_slots",
        MATCH_STATUS_WAITING_CONFIRM: "waiting_confirm",
    }

    for match in matches:
        stage = stage_map.get(match.status)
        if not stage:
            continue
        
        # Определяем момент начала стадии
        if stage == "pending_response":
            stage_start = match.created_at
        else:
            stage_start = match.updated_at
        
        if not stage_start:
            stage_start = match.created_at or now
        
        # Приводим к aware формату для корректного вычитания
        stage_start = ensure_aware_msk(stage_start) or now

        elapsed = now - stage_start

        # Пропускаем, если уже истёк таймаут (напоминания только до таймаута)
        if elapsed >= timeout_delta:
            continue

        # Проверяем, пора ли отправить напоминание
        if elapsed >= reminder_delta:
            since_last = None
            if match.last_reminder_at:
                last_reminder_aware = ensure_aware_msk(match.last_reminder_at)
                if last_reminder_aware:
                    since_last = now - last_reminder_aware
            # Отправляем напоминание если:
            # - еще не отправляли (since_last is None)
            # - или с последнего напоминания прошло >= reminder_interval_time
            if since_last is None or since_last >= reminder_delta:
                await notify_match_reminder(bot, match, stage)
                match.last_reminder_at = now
                reminded_count += 1

    await session.commit()

    return reminded_count

