"""
Регистрация фоновых задач APScheduler для мэтчинга.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.utils import MOSCOW_TZ
from app.services.matching import run_matching_round
from app.services.matching.jobs import (
    process_match_timeouts_and_reminders,
)
from app.services.matching.feedback import send_feedback_to_users
from app.services.matching.settings import (
    calculate_optimal_scheduler_interval,
    load_matching_settings,
    parse_time_to_hours_minutes,
)

logger = logging.getLogger(__name__)


async def setup_matching_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> AsyncIOScheduler:
    """
    Создаёт и конфигурирует APScheduler для фоновых задач мэтчинга.

    Регистрирует две периодические задачи:
    - еженедельный раунд мэтчинга (по настройкам match_day и match_msk_time);
    - обработка таймаутов и напоминаний (динамический интервал).

    Args:
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        bot (Bot): экземпляр бота для использования в джобах.

    Returns:
        AsyncIOScheduler: настроенный планировщик задач (не запущен, нужно вызвать start()).
    """
    scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)

    async with session_factory() as session:
        settings = await load_matching_settings(session)

    # Парсинг времени подбора из формата "ЧЧ:ММ"
    time_parts = parse_time_to_hours_minutes(settings.match_msk_time)
    if time_parts is None:
        logger.warning("Invalid match_msk_time format, using default 12:00")
        match_hour, match_minute = 12, 0
    else:
        match_hour, match_minute = time_parts

    # Cron-триггер для раунда мэтчинга
    scheduler.add_job(
        _matching_round_job,
        CronTrigger(
            day_of_week=settings.match_day or "fri",
            hour=match_hour,
            minute=match_minute,
            timezone=MOSCOW_TZ,
        ),
        args=[session_factory, bot],
        id="matching_round",
        replace_existing=True,
    )

    # Джоба напоминаний/таймаутов — динамический интервал на основе настроек
    timeout_interval, interval_unit = calculate_optimal_scheduler_interval(
        settings.reminder_interval_time,
        settings.response_timeout_time,
    )
    if interval_unit == "seconds":
        trigger = IntervalTrigger(seconds=timeout_interval, timezone=MOSCOW_TZ)
        interval_display = f"{timeout_interval:.0f} seconds"
    else:
        trigger = IntervalTrigger(minutes=timeout_interval, timezone=MOSCOW_TZ)
        interval_display = f"{timeout_interval:.1f} minutes"

    scheduler.add_job(
        _timeouts_job,
        trigger,
        args=[session_factory, bot],
        id="match_timeouts",
        replace_existing=True,
    )
    logger.info(
        "Timeouts/reminders job scheduled with interval: %s "
        "(based on reminder_interval=%s, response_timeout=%s)",
        interval_display,
        settings.reminder_interval_time,
        settings.response_timeout_time,
    )

    # Парсинг времени обратной связи из формата "ЧЧ:ММ"
    feedback_time_parts = parse_time_to_hours_minutes(settings.feedback_msk_time)
    if feedback_time_parts is None:
        logger.warning("Invalid feedback_msk_time format, using default 18:00")
        feedback_hour, feedback_minute = 18, 0
    else:
        feedback_hour, feedback_minute = feedback_time_parts

    # Cron-триггер для отправки обратной связи
    scheduler.add_job(
        _feedback_job,
        CronTrigger(
            day_of_week=settings.feedback_day or "sun",
            hour=feedback_hour,
            minute=feedback_minute,
            timezone=MOSCOW_TZ,
        ),
        args=[session_factory, bot],
        id="feedback",
        replace_existing=True,
    )

    return scheduler


async def refresh_matching_round_schedule(
    scheduler: AsyncIOScheduler,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Перечитывает match_day/match_msk_time и обновляет cron-триггер джобы.

    Используется при сохранении настроек в админке, чтобы не требовался рестарт.
    """
    async with session_factory() as session:
        settings = await load_matching_settings(session)

    # Парсинг времени подбора из формата "ЧЧ:ММ"
    time_parts = parse_time_to_hours_minutes(settings.match_msk_time)
    if time_parts is None:
        logger.warning("Invalid match_msk_time format, using default 12:00")
        match_hour, match_minute = 12, 0
    else:
        match_hour, match_minute = time_parts

    logger.info(
        "Refreshing matching round schedule: day=%s, time=%s (%d:%02d)",
        settings.match_day,
        settings.match_msk_time,
        match_hour,
        match_minute,
    )

    scheduler.reschedule_job(
        "matching_round",
        trigger=CronTrigger(
            day_of_week=settings.match_day or "fri",
            hour=match_hour,
            minute=match_minute,
            timezone=MOSCOW_TZ,
        ),
    )

    # Получаем информацию о следующем запуске для логирования
    job = scheduler.get_job("matching_round")
    if job and job.next_run_time:
        logger.info(
            "Matching round job rescheduled. Next run: %s",
            job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
    else:
        logger.warning("Matching round job not found or has no next run time")


async def refresh_timeouts_schedule(
    scheduler: AsyncIOScheduler,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Перечитывает reminder_interval_time и response_timeout_time и обновляет интервал джобы таймаутов/напоминаний.

    Используется при сохранении настроек в админке, чтобы не требовался рестарт.

    Args:
        scheduler (AsyncIOScheduler): планировщик задач.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
    """
    async with session_factory() as session:
        settings = await load_matching_settings(session)

    timeout_interval, interval_unit = calculate_optimal_scheduler_interval(
        settings.reminder_interval_time,
        settings.response_timeout_time,
    )

    if interval_unit == "seconds":
        trigger = IntervalTrigger(seconds=timeout_interval, timezone=MOSCOW_TZ)
        interval_display = f"{timeout_interval:.0f} seconds"
    else:
        trigger = IntervalTrigger(minutes=timeout_interval, timezone=MOSCOW_TZ)
        interval_display = f"{timeout_interval:.1f} minutes"

    logger.info(
        "Refreshing timeouts/reminders schedule: interval=%s "
        "(reminder_interval=%s, response_timeout=%s)",
        interval_display,
        settings.reminder_interval_time,
        settings.response_timeout_time,
    )

    scheduler.reschedule_job(
        "match_timeouts",
        trigger=trigger,
    )

    # Получаем информацию о следующем запуске для логирования
    job = scheduler.get_job("match_timeouts")
    if job and job.next_run_time:
        logger.info(
            "Timeouts/reminders job rescheduled. Next run: %s",
            job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
    else:
        logger.warning("Timeouts/reminders job not found or has no next run time")


async def _matching_round_job(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """
    Внутренняя джоба для запуска раунда мэтчинга.

    Вызывается APScheduler по расписанию (Cron-триггер).

    Args:
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        bot (Bot): экземпляр бота для отправки уведомлений.

    Returns:
        None: ничего не возвращает.
    """
    logger.info("Matching round job started by scheduler")
    try:
        async with session_factory() as session:
            await run_matching_round(session, bot)
        logger.info("Matching round job completed successfully")
    except Exception as e:
        logger.exception("Matching round job failed with error: %s", e)
        raise


async def _timeouts_job(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """
    Внутренняя джоба для обработки таймаутов и напоминаний.

    Вызывается APScheduler каждые 5 минут (IntervalTrigger).

    Args:
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        bot (Bot): экземпляр бота для отправки уведомлений.

    Returns:
        None: ничего не возвращает.
    """
    logger.debug("Timeouts and reminders job started by scheduler")
    try:
        async with session_factory() as session:
            settings = await load_matching_settings(session)
            await process_match_timeouts_and_reminders(session, settings, bot)
        logger.debug("Timeouts and reminders job completed successfully")
    except Exception as e:
        logger.exception("Timeouts and reminders job failed with error: %s", e)
        raise


async def _feedback_job(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """
    Внутренняя джоба для отправки обратной связи.

    Вызывается APScheduler по расписанию (Cron-триггер).

    Args:
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        bot (Bot): экземпляр бота для отправки уведомлений.

    Returns:
        None: ничего не возвращает.
    """
    logger.info("Feedback job started by scheduler")
    try:
        async with session_factory() as session:
            count = await send_feedback_to_users(session, bot)
        logger.info("Feedback job completed successfully. Sent to %d users", count)
    except Exception as e:
        logger.exception("Feedback job failed with error: %s", e)
        raise


async def refresh_feedback_schedule(
    scheduler: AsyncIOScheduler,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Перечитывает feedback_day/feedback_msk_time и обновляет cron-триггер джобы.

    Используется при сохранении настроек в админке, чтобы не требовался рестарт.

    Args:
        scheduler (AsyncIOScheduler): планировщик задач.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
    """
    async with session_factory() as session:
        settings = await load_matching_settings(session)

    # Парсинг времени обратной связи из формата "ЧЧ:ММ"
    time_parts = parse_time_to_hours_minutes(settings.feedback_msk_time)
    if time_parts is None:
        logger.warning("Invalid feedback_msk_time format, using default 18:00")
        feedback_hour, feedback_minute = 18, 0
    else:
        feedback_hour, feedback_minute = time_parts

    logger.info(
        "Refreshing feedback schedule: day=%s, time=%s (%d:%02d)",
        settings.feedback_day,
        settings.feedback_msk_time,
        feedback_hour,
        feedback_minute,
    )

    scheduler.reschedule_job(
        "feedback",
        trigger=CronTrigger(
            day_of_week=settings.feedback_day or "sun",
            hour=feedback_hour,
            minute=feedback_minute,
            timezone=MOSCOW_TZ,
        ),
    )

    # Получаем информацию о следующем запуске для логирования
    job = scheduler.get_job("feedback")
    if job and job.next_run_time:
        logger.info(
            "Feedback job rescheduled. Next run: %s",
            job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
    else:
        logger.warning("Feedback job not found or has no next run time")
