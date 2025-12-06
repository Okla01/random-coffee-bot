"""
Бизнес-логика управления списком пользователей в панели администратора.

Содержит функции для:
- получения списка пользователей
- блокировки/разблокировки пользователей
- управления ролями пользователей
"""

from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Role, User, UserRole, Complaint
from app.services.const import (
    ROLE_NAMES,
    USER_STATUS_ACTIVE,
    USER_STATUS_BLOCKED,
    USERS_PER_PAGE,
    USER_STATUS_NAMES,
)

# ----------------------------- Получение списка пользователей ----------------------------- #


async def get_users_page(
    session: AsyncSession,
    page: int,
    per_page: int = USERS_PER_PAGE,
    filters: dict[str, bool] | None = None,
) -> tuple[Sequence[User], int]:
    """
    Получает список пользователей для их отображения на одной странице
    панели администратора.

    Args:
        session (AsyncSession): сессия БД.
        page (int): номер страницы (1 и выше).
        per_page (int): количество пользователей на странице.
        filters (dict[str, bool] | None): фильтры по статусам, например:
            {"active": True, "blocked": False}

    Returns:
        tuple[Sequence[User], int]: (список пользователей, общее количество пользователей).
    """
    if page < 1:
        page = 1

    if filters is None:
        filters = {}

    offset = (page - 1) * per_page

    # Базовый запрос
    base_query = select(User)
    count_query = select(func.count()).select_from(User)

    # Фильтры по статусам
    filter_statuses: list[str] = []

    if filters.get("active"):
        filter_statuses.append(USER_STATUS_ACTIVE)
    if filters.get("blocked"):
        filter_statuses.append(USER_STATUS_BLOCKED)

    # Если хотя бы один фильтр включен, применяем фильтрацию
    # Если оба фильтра выключены, показываем всех пользователей
    if filter_statuses:
        base_query = base_query.where(User.status.in_(filter_statuses))
        count_query = count_query.where(User.status.in_(filter_statuses))

    # Подсчёт общего количества (с учётом фильтров)
    total_users = await session.scalar(count_query)
    total_users = total_users or 0

    # Получение конкретной страницы
    query = base_query.order_by(User.id).offset(offset).limit(per_page)

    result = await session.execute(query)
    users = result.scalars().all()

    return users, total_users


async def get_user_roles(
    session: AsyncSession,
    user_id: int,
) -> list[Role]:
    """
    Возвращает список ролей пользователя (объекты Role) через связку user_roles.
    """
    result = await session.execute(
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.name)
    )
    return result.scalars().all()


def format_user_for_admin(user: User, roles: list[Role] | None = None) -> str:
    """
    Форматирует информацию о пользователе для отображения в административной панели.

    Args:
        user (User): объект пользователя.
        roles (list[Role] | None): список ролей пользователя.
    """
    role_names = (
        [ROLE_NAMES.get(role.name, role.name) for role in roles] if roles else []
    )

    status_name = USER_STATUS_NAMES.get(user.status, user.status)
    return (
        f"👤 <b>{user.name}</b>\n"
        f"Telegram: @{user.username}\n"
        f"Зарегистрирован: {user.registered_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"Статус: {status_name}\n"
        f"Роли: {', '.join(role_names) if role_names else 'Пользователь'}\n"
    )


async def build_users_page_text(session: AsyncSession, users: list[User]) -> str:
    """
    Формирует текстовое представление страницы списка пользователей.
    """
    blocks: list[str] = []

    for user in users:
        roles = await get_user_roles(session, user.id)
        blocks.append(format_user_for_admin(user, roles))

    if not blocks:
        return "Пользователей пока нет."

    text = "\n".join(blocks)
    # Добавляем инструкцию по поиску внизу
    text += "\n\n🔍 <b>Для поиска нажмите кнопку ниже и введите <code>user:</code> + запрос:"
    text += "\n\t— <code>@username</code> — поиск по username"
    text += "\n\t— <code>123456789</code> — поиск по Telegram ID"
    text += "\n\t— <code>Имя</code> — поиск по имени в анкете</b>"

    return text


# ---------------------- Жалобы на пользователя ---------------------- #


async def get_complaints_count(
    session: AsyncSession,
    user_id: int,
) -> int:
    """
    Получает количество жалоб на пользователя.

    Args:
        session (AsyncSession): сессия БД.
        user_id (int): ID пользователя в БД.

    Returns:
        int: количество жалоб на пользователя.
    """
    result = await session.scalar(
        select(func.count()).select_from(Complaint).where(Complaint.reported_id == user_id)
    )
    return result or 0


# ---------------------- Блокировка/разблокировка пользователей ---------------------- #
