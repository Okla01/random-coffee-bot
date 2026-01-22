"""
Инициализация и управление асинхронной базой данных.

Реализует создание асинхронного движка SQLAlchemy 2.x, фабрику сессий, функции для работы
с пользователями (поиск, создание, обновление стадий), управление жизненным циклом БД
через контекстный менеджер и автоматическое создание таблиц при старте приложения.
В dev-режиме по умолчанию используется SQLite (aiosqlite), URL берётся из .env.
Поддерживает регистрацию пользовательских функций SQLite (например, casefold для работы с кириллицей).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import event, func, select

from app.database.models import User
from app.database.utils import now_msk
from app.services.core import Settings
from app.services.const import USER_STATUS_NOT_ACTIVE, USER_STATUS_BLOCKED


def make_engine(settings: Settings):
    """
    Создаёт асинхронный движок SQLAlchemy.

    Инициализирует AsyncEngine с параметрами из конфигурации, включая
    отключение echo-режима, активацию future-флага и проверку соединения перед использованием.
    
    Для PostgreSQL настраивает connection pool для поддержки высокой нагрузки (до 5000 пользователей).
    Для SQLite connection pooling не требуется.

    Args:
        settings (Settings): объект конфигурации с URL базы данных.

    Returns:
        AsyncEngine: настроенный асинхронный движок SQLAlchemy.
    """
    pool_config = {}
    
    # Настраиваем connection pool только для PostgreSQL
    # Для SQLite connection pooling бесполезен (один writer)
    if "postgresql" in settings.db_url:
        pool_config = {
            "pool_size": 50,           # Базовый размер пула соединений
            "max_overflow": 100,       # Дополнительные соединения при пиковой нагрузке
            "pool_timeout": 30,        # Таймаут ожидания свободного соединения (секунды)
            "pool_recycle": 3600,      # Переподключение каждый час (защита от stale connections)
        }
    
    return create_async_engine(
        settings.db_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        **pool_config,
    )


def make_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """
    Создаёт фабрику асинхронных сессий БД.

    Возвращает async_sessionmaker с конфигурацией, которая гарантирует,
    что объекты не истекают при коммите и используется AsyncSession.

    Args:
        engine (AsyncEngine): движок для создания сессий.

    Returns:
        async_sessionmaker[AsyncSession]: фабрика асинхронных сессий.
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    """
    Получает пользователя из БД по ID.

    Выполняет поиск пользователя по первичному ключу в базе данных.

    Args:
        session (AsyncSession): сессия БД.
        user_id (int): ID пользователя в БД.

    Returns:
        User | None: объект пользователя или None, если не найден.
    """
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    return user


async def get_user_by_tg_id(
    session: AsyncSession,
    telegram_id: int,
) -> Optional[User]:
    """
    Получает пользователя по Telegram ID.

    Выполняет поиск пользователя по уникальному Telegram ID в базе данных.
    Возвращает объект пользователя, если существует. Иначе — None.

    Args:
        session (AsyncSession): сессия БД.
        telegram_id (int): Telegram ID для поиска.

    Returns:
        Optional[User]: объект пользователя или None.
    """
    return (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()


async def search_users_by_username(
    session: AsyncSession,
    query: str,
    limit: int = 10,
) -> list[User]:
    """
    Ищет пользователей по username (частичное совпадение).

    Выполняет поиск пользователей по username с использованием case-insensitive
    частичного совпадения. Автоматически убирает символ @ из начала запроса.

    Args:
        session (AsyncSession): сессия БД.
        query (str): строка поиска (без @).
        limit (int): максимальное количество результатов.

    Returns:
        list[User]: список найденных пользователей, отсортированный по username.
    """
    # Убираем @ если есть
    query = query.lstrip("@").strip().lower()
    if not query:
        return []

    result = await session.execute(
        select(User)
        .where(User.username.ilike(f"%{query}%"))
        .order_by(User.username)
        .limit(limit)
    )
    return list(result.scalars().all())


async def search_users_by_telegram_id(
    session: AsyncSession,
    query: str,
    limit: int = 10,
) -> list[User]:
    """
    Ищет пользователей по Telegram ID (частичное совпадение).

    Выполняет поиск пользователей по частичному совпадению Telegram ID.
    Преобразует числовой ID в строку для поиска подстроки.

    Args:
        session (AsyncSession): сессия БД.
        query (str): строка поиска (часть Telegram ID).
        limit (int): максимальное количество результатов.

    Returns:
        list[User]: список найденных пользователей, отсортированный по Telegram ID.
    """
    query = query.strip()
    if not query:
        return []

    # Поиск по частичному совпадению telegram_id (приведённого к строке)
    from sqlalchemy import cast, String

    result = await session.execute(
        select(User)
        .where(cast(User.telegram_id, String).like(f"%{query}%"))
        .order_by(User.telegram_id)
        .limit(limit)
    )
    return list(result.scalars().all())


async def search_users_by_name(
    session: AsyncSession,
    query: str,
    limit: int = 10,
) -> list[User]:
    """
    Ищет пользователей по имени в анкете (частичное совпадение).

    Выполняет поиск пользователей по имени с использованием case-insensitive
    частичного совпадения через функцию casefold для корректной работы с кириллицей.

    Args:
        session (AsyncSession): сессия БД.
        query (str): строка поиска (часть имени).
        limit (int): максимальное количество результатов.

    Returns:
        list[User]: список найденных пользователей, отсортированный по имени.
    """
    query = query.strip()
    if not query:
        return []

    needle = query.casefold()

    result = await session.execute(
        select(User)
        .where(func.py_casefold(User.name).like(f"%{needle}%"))
        .order_by(User.name)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
) -> User:
    """
    Получает пользователя по Telegram ID или создаёт нового.

    Если пользователь существует — возвращает его и обновляет username при изменении.
    Если нет — создаёт новую запись с переданными параметрами и возвращает объект.
    Не коммитит изменения (требуется явный commit в вызывающем коде).

    Args:
        session (AsyncSession): сессия БД.
        telegram_id (int): Telegram ID пользователя.
        username (Optional[str]): имя пользователя в Telegram (опционально).

    Returns:
        User: объект пользователя (новый или существующий).
    """
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
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

    Сравнивает статус пользователя с константой USER_STATUS_BLOCKED.
    Возвращает False, если пользователь не передан или статус отсутствует.

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

    Изменяет поле stage пользователя, опционально обновляет данные в FSM-состоянии
    и коммитит изменения в базе данных. Примечание: last_activity обновляется
    автоматически в BlockedUserMiddleware.

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

    Асинхронный контекстный менеджер, который создаёт движок БД, регистрирует
    пользовательские функции SQLite (например, casefold для работы с кириллицей),
    инициализирует таблицы на основе моделей SQLAlchemy, инициализирует дефолтные
    настройки, предоставляет фабрику сессий для использования и корректно
    освобождает ресурсы при завершении.

    Args:
        settings (Settings): объект конфигурации.

    Yields:
        async_sessionmaker[AsyncSession]: фабрика асинхронных сессий.

    Raises:
        Exception: если не удаётся подключиться к БД или создать таблицы.
    """
    engine = make_engine(settings)

    # Регистрация функции casefold для SQLite
    # Это нужно для корректной работы с кириллицей в SQLite
    def _register_casefold(dbapi_conn, _):
        # Работает только для SQLite. Для Postgres/MySQL просто не регистрируем.
        try:
            dbapi_conn.create_function(
                "py_casefold",
                1,
                lambda s: s.casefold() if s is not None else None,
            )
        except Exception:
            # если это не sqlite соединение или драйвер не поддерживает
            pass

    event.listen(engine.sync_engine, "connect", _register_casefold)

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
