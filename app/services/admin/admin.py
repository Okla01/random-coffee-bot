"""
Бизнес-логика обработки команды /admin.

Содержит функции для:
- обработки запроса на открытие административной панели
- проверки прав и создания пользователя при необходимости
- логирования действий администратора
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core import Settings
from app.database.db import get_user_by_tg_id
from app.database import User, AdminLog
from .roles import sync_admin_role, is_admin
from app.services.const import USER_STATUS_NEW


class AdminAccessResultType(str, Enum):
    """Типы результатов проверки доступа к админ-панели."""

    SUCCESS = "success"
    NO_RIGHTS = "no_rights"
    USER_CREATED = "user_created"


async def process_admin_command(
    session: AsyncSession,
    settings: Settings,
    telegram_id: int,
    username: str | None,
) -> tuple[AdminAccessResultType, User | None]:
    """
    Обрабатывает запрос на открытие административной панели.

    Проверяет права администратора, создаёт пользователя при необходимости
    (если telegram_id в ADMIN_IDS), синхронизирует роль, проверяет статус блокировки,
    логирует открытие панели и обновляет last_activity.

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        telegram_id (int): Telegram ID пользователя.
        username (str | None): имя пользователя в Telegram.

    Returns:
        tuple[AdminAccessResultType, User | None]: (тип_результата, объект_пользователя_или_None).
    """
    user = await get_user_by_tg_id(session, telegram_id)
    user_created = False

    if not user:
        # Создаём пользователя, если tg_id ∈ ADMIN_IDS (ТЗ 8.3)
        if telegram_id in settings.admin_ids:
            user = User(
                telegram_id=telegram_id,
                username=username,
                status=USER_STATUS_NEW,
                stage="new",
            )
            session.add(user)
            await session.flush()
            user_created = True
        else:
            return AdminAccessResultType.NO_RIGHTS, None

    # Синхронизируем роль администратора если пользователь в ADMIN_IDS
    await sync_admin_role(session, settings, user)

    # Проверка наличия прав администратора
    if not await is_admin(session, settings, telegram_id):
        return AdminAccessResultType.NO_RIGHTS, user

    # Логирование открытия админ-панели
    session.add(
        AdminLog(
            admin_id=telegram_id,
            action="open_admin",
            payload={"user_id": user.id},
        )
    )
    await session.flush()

    result_type = (
        AdminAccessResultType.USER_CREATED
        if user_created
        else AdminAccessResultType.SUCCESS
    )
    return result_type, user
