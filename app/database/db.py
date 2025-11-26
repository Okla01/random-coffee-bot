"""
Инициализация асинхронной БД (SQLAlchemy 2.x, async).
В dev по умолчанию — SQLite (aiosqlite), URL берётся из .env.
Создание таблиц происходит автоматически при старте (для prod — миграции).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.core import Settings


def make_engine(settings: Settings):
    """
    Создаёт асинхронный движок SQLAlchemy.

    Инициализирует AsyncEngine с параметрами из конфигурации, включая
    отключение echo-режима, активацию future-флага и проверку соединения перед использованием.

    Args:
        settings (Settings): объект конфигурации с URL базы данных.

    Returns:
        AsyncEngine: настроенный асинхронный движок SQLAlchemy.
    """
    return create_async_engine(
        settings.db_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
    )


def make_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """
    Создаёт фабрику асинхронных сессий БД.

    Возвращает async_sessionmaker с конфигурацией, которая гарантирует,
    что объекты не истекают при коммите и используется AsyncSession.

    Args:
        engine: AsyncEngine для создания сессий.

    Returns:
        async_sessionmaker[AsyncSession]: фабрика асинхронных сессий.
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def lifespan_db(
    settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """
    Управляет жизненным циклом подключения к базе данных.

    Асинхронный контекстный менеджер, который создаёт движок БД,
    инициализирует таблицы на основе моделей SQLAlchemy, предоставляет
    фабрику сессий для использования, и корректно освобождает ресурсы при завершении.

    Args:
        settings (Settings): объект конфигурации.

    Returns:
        AsyncIterator[async_sessionmaker[AsyncSession]]: итератор фабрики сессий.

    Raises:
        Exception: если не удаётся подключиться к БД или создать таблицы.
    """
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)

    from .models import Base as _Base  # импорт отложенно, чтобы не образовать циклы

    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)

    # Инициализируем дефолтные настройки
    async with session_factory() as session:
        from .init_settings import init_default_settings
        await init_default_settings(session)
        await session.commit()

    try:
        yield session_factory
    finally:
        await engine.dispose()
