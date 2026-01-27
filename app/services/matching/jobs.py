"""
Периодические задачи домена мэтчинга.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import Match, User
from app.database.utils import ensure_aware_msk, now_msk
from app.services.matching.constants import (
    MATCH_STATUS_EXPIRED_TIMEOUT,
    MATCH_STATUS_PENDING_RESPONSE,
    MATCH_USER_RESPONSE_SKIP,
)
from app.services.matching.messages import (
    notify_match_reminder,
    notify_match_timeout,
    remove_match_keyboards,
)
from app.services.matching.settings import MatchingSettings, parse_time_to_hours
from app.services.matching.storage import cleanup_inactive_match
from app.services.matching.round import _send_match_invite

logger = logging.getLogger(__name__)




async def _get_users_to_remind(
    session: AsyncSession, match: Match, stage: str
) -> list[User]:
    """
    Определяет список пользователей, которым нужно отправить напоминание на текущем этапе.

    Args:
        session (AsyncSession): активная сессия БД.
        match (Match): объект мэтча с загруженными user_a и user_b.
        stage (str): текущая стадия мэтча (pending_response).

    Returns:
        list: список пользователей (User), которым нужно отправить напоминание.
    """
    users_to_remind = []

    if stage == "pending_response":
        # Напоминаем только тем, кто еще не ответил (response is None)
        if match.user_a and match.user_a_response is None:
            users_to_remind.append(match.user_a)
        if match.user_b and match.user_b_response is None:
            users_to_remind.append(match.user_b)

    return users_to_remind


async def process_match_timeouts_and_reminders(
    session: AsyncSession,
    settings: MatchingSettings,
    bot: Bot | None = None,
) -> dict[str, int]:
    """
    Обрабатывает напоминания и таймауты для мэтчей в активных стадиях.

    Проверяет мэтчи со статусом pending_response:
    - отправляет напоминания с интервалом reminder_interval_time;
    - переводит в expired_timeout при превышении response_timeout_time.

    Args:
        session (AsyncSession): активная сессия БД.
        settings (MatchingSettings): настройки мэтчинга (таймауты, интервалы напоминаний).
        bot (Bot | None): экземпляр бота для отправки уведомлений (опционально).

    Returns:
        dict[str, int]: статистика обработки вида {"reminded": X, "expired": Y}.
    """
    now = now_msk()
    # Конвертация из формата ЧЧ:ММ в часы для timedelta
    reminder_hours = parse_time_to_hours(settings.reminder_interval_time)
    timeout_hours = parse_time_to_hours(settings.response_timeout_time)
    reminder_delta = timedelta(hours=max(0, reminder_hours))
    timeout_delta = timedelta(hours=max(0, timeout_hours))

    stats = {"reminded": 0, "expired": 0}
    stage_map = {
        MATCH_STATUS_PENDING_RESPONSE: "pending_response",
    }

    # ОПТИМИЗАЦИЯ: Фильтруем мэтчи по времени создания в SQL
    # Загружаем только те мэтчи, которые созданы после earliest_time
    # (мэтчи старше timeout_delta точно истекли и не нуждаются в проверке)
    earliest_time = now - timeout_delta
    
    stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(
            Match.status.in_(stage_map.keys()),
            Match.created_at >= earliest_time,  # Фильтр по времени в SQL
        )
        .order_by(Match.created_at)  # Сортировка для предсказуемости обработки
    )
    result = await session.execute(stmt)
    matches = list(result.scalars().all())

    for match in matches:
        stage = stage_map[match.status]

        # Определяем момент начала стадии согласно ТЗ:
        # - для pending_response: created_at (момент нахождения пары)
        if stage == "pending_response":
            stage_start = match.created_at

        if not stage_start:
            stage_start = match.created_at or now

        # Приводим к aware формату для корректного вычитания
        stage_start = ensure_aware_msk(stage_start) or now

        elapsed = now - stage_start

        # Проверяем таймаут: если прошло больше response_timeout_time
        if elapsed >= timeout_delta:
            match.status = MATCH_STATUS_EXPIRED_TIMEOUT
            # Устанавливаем skip только если пользователь еще не ответил
            if match.user_a_response is None:
                match.user_a_response = MATCH_USER_RESPONSE_SKIP
            if match.user_b_response is None:
                match.user_b_response = MATCH_USER_RESPONSE_SKIP
            match.last_reminder_at = None
            # Удаляем клавиатуры из старых сообщений перед очисткой данных
            if bot:
                await remove_match_keyboards(bot, match)
            # Очищаем данные неактивного мэтча
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
                users_to_remind = await _get_users_to_remind(session, match, stage)
                if users_to_remind:
                    await notify_match_reminder(bot, match, stage, users_to_remind)
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
    Проверяет и обрабатывает только таймауты для мэтчей в активных стадиях.

    Проверяет мэтчи с активными статусами и переводит их в expired_timeout
    при превышении response_timeout_time.

    Args:
        session (AsyncSession): активная сессия БД.
        settings (MatchingSettings): настройки мэтчинга (таймауты).
        bot (Bot | None): экземпляр бота для отправки уведомлений (опционально).

    Returns:
        int: количество истёкших мэтчей.
    """
    now = now_msk()
    timeout_hours = parse_time_to_hours(settings.response_timeout_time)
    timeout_delta = timedelta(hours=max(0, timeout_hours))

    # Статусы, для которых проверяются таймауты (исключаем matched, там нет таймаутов)
    timeout_check_statuses = {
        MATCH_STATUS_PENDING_RESPONSE,
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
            # Устанавливаем skip только если пользователь еще не ответил
            if match.user_a_response is None:
                match.user_a_response = MATCH_USER_RESPONSE_SKIP
            if match.user_b_response is None:
                match.user_b_response = MATCH_USER_RESPONSE_SKIP
            match.last_reminder_at = None
            # Удаляем клавиатуры из старых сообщений перед очисткой данных
            if bot:
                await remove_match_keyboards(bot, match)
            # Очищаем данные неактивного мэтча
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
    Проверяет и отправляет только напоминания для мэтчей в активных стадиях.

    Проверяет мэтчи с активными статусами и отправляет напоминания,
    если прошло достаточно времени с момента последнего напоминания.

    Args:
        session (AsyncSession): активная сессия БД.
        settings (MatchingSettings): настройки мэтчинга (интервалы напоминаний).
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
    timeout_delta = timedelta(hours=max(0, timeout_hours))

    if reminder_delta <= timedelta(0):
        return 0

    # Статусы, для которых отправляются напоминания (исключаем matched, там нет напоминаний)
    reminder_statuses = {
        MATCH_STATUS_PENDING_RESPONSE,
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
                users_to_remind = await _get_users_to_remind(session, match, stage)
                if users_to_remind:
                    await notify_match_reminder(bot, match, stage, users_to_remind)
                    match.last_reminder_at = now
                    reminded_count += 1

    await session.commit()

    return reminded_count


async def resend_failed_match_notifications(
    session: AsyncSession,
    bot: Bot | None = None,
) -> int:
    """
    Повторно отправляет уведомления о создании мэтчей, которые не были доставлены.

    Проверяет мэтчи со статусом pending_response, у которых флаги notified_a или notified_b = False,
    и пытается отправить уведомления повторно.

    Args:
        session (AsyncSession): активная сессия БД.
        bot (Bot | None): экземпляр бота для отправки уведомлений (опционально).

    Returns:
        int: количество успешно отправленных уведомлений.
    """
    if bot is None:
        return 0

    stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(
            Match.status == MATCH_STATUS_PENDING_RESPONSE,
            (Match.notified_a == False) | (Match.notified_b == False),
        )
    )
    result = await session.execute(stmt)
    matches = list(result.scalars().all())

    sent_count = 0
    for match in matches:
        try:
            # Пытаемся отправить уведомление user_a, если не было отправлено
            if not match.notified_a and match.user_a and match.user_a.telegram_id:
                try:
                    await _send_match_invite(session, bot, match, is_user_a=True)
                    sent_count += 1
                except Exception as e:
                    logger.warning(
                        "Failed to resend notification to user_a (match %s, user %s): %s",
                        match.id,
                        match.user_a_id,
                        e,
                    )

            # Пытаемся отправить уведомление user_b, если не было отправлено
            if not match.notified_b and match.user_b and match.user_b.telegram_id:
                try:
                    await _send_match_invite(session, bot, match, is_user_a=False)
                    sent_count += 1
                except Exception as e:
                    logger.warning(
                        "Failed to resend notification to user_b (match %s, user %s): %s",
                        match.id,
                        match.user_b_id,
                        e,
                    )
        except Exception as e:
            logger.exception("Error processing match %s for resend: %s", match.id, e)

    await session.commit()
    return sent_count
