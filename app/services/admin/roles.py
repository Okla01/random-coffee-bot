"""
Бизнес-логика управления ролями администраторов.

Содержит функции для:
- синхронизации ролей администраторов
- проверки прав администратора
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core import Settings
from app.database import User, Role, UserRole
from app.database.db import get_user_by_tg_id
from app.services.const import ROLE_ADMIN


async def sync_admin_role(
    session: AsyncSession, settings: Settings, user: User
) -> None:
    """
    Синхронизирует роль администратора для пользователя.

    Если пользователь указан в ADMIN_IDS конфигурации, добавляет ему роль 'admin'.
    Создаёт роль при необходимости. Не удаляет роль если пользователь исключён из ADMIN_IDS.

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        user (User): объект пользователя.

    Returns:
        None: ничего не возвращает.
    """
    if user.telegram_id not in settings.admin_ids:
        return

    # Получаем или создаём роль admin
    role = (
        await session.execute(select(Role).where(Role.name == ROLE_ADMIN))
    ).scalar_one_or_none()
    if not role:
        role = Role(name=ROLE_ADMIN)
        session.add(role)
        await session.flush()

    # Проверяем, есть ли уже связь пользователь-роль
    link = (
        await session.execute(
            select(UserRole).where(
                UserRole.user_id == user.id, UserRole.role_id == role.id
            )
        )
    ).scalar_one_or_none()
    if not link:
        session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.flush()


async def is_admin(session: AsyncSession, settings: Settings, tg_id: int) -> bool:
    """
    Проверяет, является ли пользователь администратором.

    Пользователь считается администратором, если:
    - его Telegram ID находится в ADMIN_IDS конфигурации, или
    - у него есть роль 'admin' в БД.

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        tg_id (int): Telegram ID пользователя для проверки.

    Returns:
        bool: True если пользователь администратор, иначе False.
    """
    # Проверяем в ADMIN_IDS конфигурации
    if tg_id in settings.admin_ids:
        return True

    # Проверяем роль в БД
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return False

    q = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id, Role.name == ROLE_ADMIN)
    )
    return (await session.execute(q)).scalar_one_or_none() is not None

