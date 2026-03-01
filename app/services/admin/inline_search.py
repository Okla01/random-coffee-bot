"""
Бизнес-логика inline-поиска пользователей в административной панели.

Содержит функции для:
- формирования результатов inline-поиска
- обработки выбранного результата поиска
- формирования медиа-групп с фотографиями пользователей
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.admin.users import get_user_roles, get_complaints_count
from app.services.const import (
    IS_RESULT_KEY_HAS_PHOTOS,
    IS_RESULT_KEY_DESCRIPTION,
    IS_RESULT_KEY_ID,
    IS_RESULT_KEY_MESSAGE_TEXT,
    IS_RESULT_KEY_TITLE,
    ROLE_NAMES,
    UPD_KEY_HAS_PHOTOS,
    UPD_KEY_PHOTOS_LIST,
    UPD_KEY_PROFILE_TEXT,
    USER_STATUS_NAMES,
)
from app.services.photo import get_photo_count, has_photos

if TYPE_CHECKING:
    from aiogram.types import InputMediaPhoto


# ----------------------------- Формирование результатов поиска ----------------------------- #


def build_user_profile_text(
    user: User, roles_str: str, status_str: str, complaints_count: int = 0
) -> str:
    """
    Формирует полный текст профиля пользователя для отображения.

    Args:
        user (User): объект пользователя.
        roles_str (str): строковое представление ролей пользователя.
        status_str (str): строковое представление статуса пользователя.
        complaints_count (int): количество жалоб на пользователя.

    Returns:
        str: отформатированный текст профиля.
    """
    text_lines = [
        "👤 <b>Профиль пользователя</b>",
        "",
        f"Telegram ID: <code>{user.telegram_id}</code>",
        f"Username: @{user.username}" if user.username else "Username: —",
        "",
        f"Статус: {status_str}",
        f"Роли: {roles_str}",
        "",
        f"Имя в анкете: {user.name or '—'}",
        f"Возраст: {user.age or '—'}",
        f"Описание: {user.bio or '—'}",
        "",
        f"Зарегистрирован: {user.registered_at:%d.%m.%Y %H:%M}",
        f"Последняя активность: {user.last_activity:%d.%m.%Y %H:%M}",
        "",
        f"⚠️ Жалоб на пользователя: {complaints_count}",
    ]
    return "\n".join(text_lines)


def build_user_search_description(
    user: User, roles_str: str, status_str: str, photos_count: int
) -> str:
    """
    Формирует описание пользователя для предпросмотра в результатах поиска.

    Args:
        user (User): объект пользователя.
        roles_str (str): строковое представление ролей пользователя.
        status_str (str): строковое представление статуса пользователя.
        photos_count (int): количество фотографий пользователя.

    Returns:
        str: описание для предпросмотра.
    """
    description = f"{status_str} | Роли: {roles_str}"
    if photos_count > 0:
        description += f" | 📷 {photos_count} фото"
    return description


async def prepare_inline_search_result(
    session: AsyncSession, user: User, search_type: str = "username"
) -> dict[str, str | int]:
    """
    Подготавливает данные пользователя для результата inline-поиска.

    Args:
        session (AsyncSession): сессия БД.
        user (User): объект пользователя.
        search_type (str): тип поиска ("username", "id", "name").

    Returns:
        dict: словарь с данными для формирования результата поиска.
    """
    # Получение ролей пользователя
    roles = await get_user_roles(session, user.id)
    roles_str = (
        ", ".join(ROLE_NAMES.get(r.name, r.name) for r in roles) if roles else "нет"
    )
    status_str = USER_STATUS_NAMES.get(user.status, user.status)

    # Получение количества фотографий
    photos_count = get_photo_count(user)

    # Формирование описания
    description = build_user_search_description(
        user, roles_str, status_str, photos_count
    )

    # Формирование заголовка в зависимости от типа поиска
    if search_type == "username":
        title = f"@{user.username}" if user.username else f"ID: {user.telegram_id}"
    else:
        title = user.name if user.name else "—"

    # Формирование текста для автоматической отправки
    shown_username = f"@{user.username}" if user.username else "—"
    message_text = f"Анкета пользователя {shown_username}"

    return {
        IS_RESULT_KEY_ID: str(user.id),
        IS_RESULT_KEY_TITLE: title,
        IS_RESULT_KEY_DESCRIPTION: description,
        IS_RESULT_KEY_MESSAGE_TEXT: message_text,
        IS_RESULT_KEY_HAS_PHOTOS: photos_count > 0,
    }


# ----------------------------- Подготовка данных анкеты пользователя ----------------------------- #


async def prepare_user_profile_data(session: AsyncSession, user: User) -> dict:
    """
    Подготавливает данные анкеты пользователя для отображения.

    Args:
        session (AsyncSession): сессия БД.
        user (User): объект пользователя.

    Returns:
        dict: словарь с данными профиля.
    """
    # Получение ролей пользователя
    roles = await get_user_roles(session, user.id)
    roles_str = (
        ", ".join(ROLE_NAMES.get(r.name, r.name) for r in roles) if roles else "нет"
    )
    status_str = USER_STATUS_NAMES.get(user.status, user.status)

    # Получение количества жалоб на пользователя
    complaints_count = await get_complaints_count(session, user.id)

    # Формирование текста профиля
    profile_text = build_user_profile_text(user, roles_str, status_str, complaints_count)

    return {
        UPD_KEY_PROFILE_TEXT: profile_text,
        UPD_KEY_PHOTOS_LIST: None,  # Больше не передаём список файлов
        UPD_KEY_HAS_PHOTOS: has_photos(user),
    }
