"""
Утилиты для работы с пользователями и их состоянием.

Содержит функции для получения/создания пользователей, проверки блокировки,
валидации стадий, и обновления информации пользователя.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.fsm.context import FSMContext

from app.database import User
from app.database.utils import now_utc


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
        user.last_activity = now_utc()
        return user

    # Создаём нового пользователя
    user = User(
        telegram_id=telegram_id,
        username=username,
        status="new",
        stage="new",
        last_activity=now_utc(),
    )
    session.add(user)
    await session.flush()
    return user


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


async def check_user_blocked(
    cq,
    session: AsyncSession,
    user: User,
) -> bool:
    """
    Проверяет, заблокирован ли пользователь, и отправляет уведомление если заблокирован.

    Если статус пользователя 'blocked', отправляет сообщение и возвращает True.
    Иначе возвращает False. Используется для ранней проверки в обработчиках.

    Args:
        cq: callback запрос для отправки сообщения.
        session (AsyncSession): сессия БД.
        user (User): объект пользователя для проверки.

    Returns:
        bool: True если пользователь заблокирован, иначе False.
    """
    if user.status == "blocked":
        await cq.message.answer(
            "Доступ временно заблокирован. Свяжитесь с администратором."
        )
        await session.commit()
        return True
    return False


def is_stage_valid(user: User, valid_stages: set[str]) -> bool:
    """
    Проверяет, находится ли пользователь на одной из допустимых стадий.

    Простая проверка текущей стадии пользователя против набора разрешённых стадий.

    Args:
        user (User): объект пользователя.
        valid_stages (set[str]): набор допустимых стадий.

    Returns:
        bool: True если текущая стадия входит в valid_stages, иначе False.
    """
    return user.stage in valid_stages


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
    user.last_activity = now_utc()
    await session.commit()

    if state_data:
        await state.update_data(**state_data)

