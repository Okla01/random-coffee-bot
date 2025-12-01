"""
Инициализация асинхронной БД (SQLAlchemy 2.x, async).
В dev по умолчанию — SQLite (aiosqlite), URL берётся из .env.
Создание таблиц происходит автоматически при старте (для prod — миграции).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.database.models import User
from app.database.utils import now_msk
from app.services.core import Settings
from app.services.const import USER_STATUS_NOT_ACTIVE, USER_STATUS_BLOCKED


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


async def get_user_by_tg_id(
    session: AsyncSession,
    telegram_id: int,
) -> Optional[User]:
    """
    Получает пользователя по Telegram ID.

    Возвращает объект пользователя, если существует. Иначе — None.

    Args:
        session (AsyncSession): сессия БД.
        telegram_id (int): Telegram ID для поиска.

    Returns:
        Optional[User]: объект пользователя или None.
    """
    return (
        await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
    ).scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
) -> User:
    """
    Получает пользователя по Telegram ID или создаёт нового.

    Если пользователь существует — возвращает его и обновляет username при изменении.
    Если нет — создаёт новую запись с переданными параметрами и возвращает объект.
    Не коммитит изменения.

    Args:
        session (AsyncSession): сессия БД.
        telegram_id (int): Telegram ID пользователя.
        username (Optional[str]): имя пользователя в Telegram (опционально).

    Returns:
        User: объект пользователя (новый или существующий).
    """
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()

    if user:
        # Обновляем username, если он изменился
        if user.username != username:
            user.username = username
        # Теоретически telegram_id меняться не должен, но на всякий случай:
        if user.telegram_id != telegram_id:
            user.telegram_id = telegram_id
        return user

    # Создаём нового пользователя
    user = User(
        telegram_id=telegram_id,
        username=username,
        status=USER_STATUS_NOT_ACTIVE,
        stage="new",
        last_activity=now_msk(),
    )
    session.add(user)
    await session.flush()
    return user


def is_user_blocked(user: User) -> bool:
    """
    Проверяет, заблокирован ли пользователь.

    Args:
        user (User): объект пользователя для проверки.

    Returns:
        bool: True если пользователь заблокирован, иначе False.
    """
    if not user or not user.status:
        return False
    return user.status.strip().lower() == USER_STATUS_BLOCKED


async def update_user_stage(
    session: AsyncSession,
    user: User,
    new_stage: str,
    state: FSMContext,
    state_data: dict | None = None,
) -> None:
    """
    Обновляет стадию пользователя и обновляет FSM-состояние.

    Изменяет поле stage пользователя, обновляет last_activity, опционально
    обновляет данные в FSM-состоянии и коммитит изменения.

    Args:
        session (AsyncSession): сессия БД.
        user (User): объект пользователя.
        new_stage (str): новая стадия для установки.
        state (FSMContext): контекст FSM для обновления.
        state_data (dict | None): дополнительные данные для FSM (опционально).

    Returns:
        None: ничего не возвращает.
    """
    user.stage = new_stage
    await session.commit()

    if state_data:
        await state.update_data(**state_data)

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
