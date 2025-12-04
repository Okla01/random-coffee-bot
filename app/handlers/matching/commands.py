"""
Вспомогательные команды для отладки матчинга. Дев
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sqlalchemy import delete, update

from app.database import Match, User, MatchSlot
from app.services.admin.roles import is_admin
from app.services.core import Settings
from app.services.matching import run_matching_round
from app.services.matching.jobs import (
    process_match_timeouts_only,
    process_match_reminders_only,
)
from app.services.matching.settings import load_matching_settings

router = Router()


@router.message(Command("test_matching"))
async def cmd_test_matching(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Принудительно запускает раунд матчинга (доступно только администраторам).

    Args:
        message (Message): объект сообщения от пользователя.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        settings (Settings): настройки приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Запускаю тестовый раунд матчинга...")

    async with session_factory() as session:
        await run_matching_round(session, message.bot)


@router.message(Command("reset_matching"))
async def cmd_reset_matching(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Очищает все записи в таблице matches и сбрасывает last_pairing_at у всех пользователей.

    Доступно только администраторам. Используется для полного сброса состояния матчинга.

    Args:
        message (Message): объект сообщения от пользователя.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        settings (Settings): настройки приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Очищаю все матчи и сбрасываю last_pairing_at...")

    async with session_factory() as session:
        # Удаляем все записи из matches
        await session.execute(delete(Match))
        await session.execute(delete(MatchSlot))
        # Очищаем last_pairing_at и last_match_at у всех пользователей
        await session.execute(update(User).values(last_pairing_at=None, last_match_at=None))
        
        await session.commit()

    await message.answer("✅ Все матчи удалены, last_pairing_at сброшен у всех пользователей.")


@router.message(Command("test_scheduler"))
async def cmd_test_scheduler(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Немедленно запускает джобу матчинга из планировщика (доступно только администраторам).

    Полезно для тестирования работы планировщика без ожидания наступления времени.

    Args:
        message (Message): объект сообщения от пользователя.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        settings (Settings): настройки приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Запускаю джобу матчинга из планировщика...")

    from app.services.matching.scheduler import _matching_round_job

    await _matching_round_job(session_factory, message.bot)
    await message.answer("✅ Джоба выполнена.")


@router.message(Command("test_timeouts"))
async def cmd_test_timeouts(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Принудительно проверяет таймауты для матчей в активных статусах (доступно только администраторам).

    Проверяет все матчи с активными статусами и переводит их в expired_timeout,
    если истёк таймаут ответа.

    Args:
        message (Message): объект сообщения от пользователя.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        settings (Settings): настройки приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Проверяю таймауты для активных матчей...")

    async with session_factory() as session:
        matching_settings = await load_matching_settings(session)
        expired_count = await process_match_timeouts_only(
            session, matching_settings, message.bot
        )

    await message.answer(
        f"✅ Проверка таймаутов завершена. Истёкших матчей: {expired_count}"
    )


@router.message(Command("test_reminder"))
async def cmd_test_reminder(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Принудительно проверяет и отправляет напоминания для матчей в активных статусах (доступно только администраторам).

    Проверяет все матчи с активными статусами и отправляет напоминания,
    если прошло достаточно времени с момента последнего напоминания.

    Args:
        message (Message): объект сообщения от пользователя.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.
        settings (Settings): настройки приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Проверяю и отправляю напоминания для активных матчей...")

    async with session_factory() as session:
        matching_settings = await load_matching_settings(session)
        reminded_count = await process_match_reminders_only(
            session, matching_settings, message.bot
        )

    await message.answer(
        f"✅ Проверка напоминаний завершена. Отправлено напоминаний: {reminded_count}"
    )

