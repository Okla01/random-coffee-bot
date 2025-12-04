"""
Регистрация фоновых задач APScheduler для матчинга.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.utils import MOSCOW_TZ
from app.services.matching import run_matching_round
from app.services.matching.jobs import (
    complete_due_meetings,
    process_match_timeouts_and_reminders,
)
from app.services.matching.settings import load_matching_settings


async def setup_matching_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> AsyncIOScheduler:
    """
    Создаёт и конфигурирует APScheduler для фоновых задач матчинга.

    Регистрирует три периодические задачи:
    - еженедельный раунд матчинга (по настройкам match_day и match_msk_hour);
    - завершение наступивших встреч (каждые 5 минут);
    - обработка таймаутов и напоминаний (каждые 30 минут).

    Args:
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        bot (Bot): экземпляр бота для использования в джобах.

    Returns:
        AsyncIOScheduler: настроенный планировщик задач (не запущен, нужно вызвать start()).
    """
    scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)

    async with session_factory() as session:
        settings = await load_matching_settings(session)

    # Cron-триггер для раунда матчинга
    scheduler.add_job(
        _matching_round_job,
        CronTrigger(
            day_of_week=settings.match_day or "fri",
            hour=settings.match_msk_hour,
            minute=0,
            timezone=MOSCOW_TZ,
        ),
        args=[session_factory, bot],
        id="matching_round",
        replace_existing=True,
    )

    # Джоба завершения встреч — каждые 5 минут
    scheduler.add_job(
        _complete_meetings_job,
        IntervalTrigger(minutes=5, timezone=MOSCOW_TZ),
        args=[session_factory, bot],
        id="complete_meetings",
        replace_existing=True,
    )

    # Джоба напоминаний/таймаутов — каждые 30 минут
    scheduler.add_job(
        _timeouts_job,
        IntervalTrigger(minutes=30, timezone=MOSCOW_TZ),
        args=[session_factory, bot],
        id="match_timeouts",
        replace_existing=True,
    )

    return scheduler


async def _matching_round_job(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """
    Внутренняя джоба для запуска раунда матчинга.

    Вызывается APScheduler по расписанию (Cron-триггер).

    Args:
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        bot (Bot): экземпляр бота для отправки уведомлений.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        await run_matching_round(session, bot)


async def _complete_meetings_job(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """
    Внутренняя джоба для завершения наступивших встреч.

    Вызывается APScheduler каждые 5 минут (IntervalTrigger).

    Args:
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        bot (Bot): экземпляр бота для отправки уведомлений.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        await complete_due_meetings(session, bot)


async def _timeouts_job(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """
    Внутренняя джоба для обработки таймаутов и напоминаний.

    Вызывается APScheduler каждые 30 минут (IntervalTrigger).

    Args:
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        bot (Bot): экземпляр бота для отправки уведомлений.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        settings = await load_matching_settings(session)
        await process_match_timeouts_and_reminders(session, settings, bot)

