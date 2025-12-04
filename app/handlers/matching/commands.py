"""
Вспомогательные команды для отладки матчинга.
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
        # Очищаем last_pairing_at у всех пользователей
        await session.execute(update(User).values(last_pairing_at=None))
        
        await session.commit()

    await message.answer("✅ Все матчи удалены, last_pairing_at сброшен у всех пользователей.")

