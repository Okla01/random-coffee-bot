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
from app.services.admin.users import get_user_roles
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
from app.services.profile.photo import get_photos_list

if TYPE_CHECKING:
    from aiogram.types import InputMediaPhoto

# ----------------------------- Формирование результатов поиска ----------------------------- #


def build_user_profile_text(user: User, roles_str: str, status_str: str) -> str:
    """
    Формирует полный текст профиля пользователя для отображения.

    Args:
        user (User): объект пользователя.
        roles_str (str): строковое представление ролей пользователя.
        status_str (str): строковое представление статуса пользователя.

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
        dict: словарь с данными для формирования результата поиска:
            - IS_RESULT_KEY_ID: ID пользователя (str)
            - IS_RESULT_KEY_TITLE: заголовок результата (str)
            - IS_RESULT_KEY_DESCRIPTION: описание для предпросмотра (str)
            - IS_RESULT_KEY_MESSAGE_TEXT: текст для автоматической отправки (str)
            - IS_RESULT_KEY_HAS_PHOTOS: есть ли фотографии (bool)
    """
    # Получение ролей пользователя
    roles = await get_user_roles(session, user.id)
    roles_str = (
        ", ".join(ROLE_NAMES.get(r.name, r.name) for r in roles) if roles else "нет"
    )
    status_str = USER_STATUS_NAMES.get(user.status, user.status)

    # Получение фотографий пользователя
    photos_list = get_photos_list(user)
    photos_count = len(photos_list)

    # Формирование описания
    description = build_user_search_description(
        user, roles_str, status_str, photos_count
    )

    # Формирование заголовка в зависимости от типа поиска
    if search_type == "username":
        title = f"@{user.username}" if user.username else f"ID: {user.telegram_id}"
    else:
        # Для поиска по ID или имени показываем имя в анкете
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
        dict: словарь с данными профиля:
            - UPD_KEY_PROFILE_TEXT: полный текст профиля (str)
            - UPD_KEY_PHOTOS_LIST: список фотографий (list)
            - UPD_KEY_HAS_PHOTOS: есть ли фотографии (bool)
    """
    # Получение ролей пользователя
    roles = await get_user_roles(session, user.id)
    roles_str = (
        ", ".join(ROLE_NAMES.get(r.name, r.name) for r in roles) if roles else "нет"
    )
    status_str = USER_STATUS_NAMES.get(user.status, user.status)

    # Формирование текста профиля
    profile_text = build_user_profile_text(user, roles_str, status_str)

    # Получение фотографий пользователя
    photos_list = get_photos_list(user)

    return {
        UPD_KEY_PROFILE_TEXT: profile_text,
        UPD_KEY_PHOTOS_LIST: photos_list,
        UPD_KEY_HAS_PHOTOS: len(photos_list) > 0,
    }


def build_media_group(
    photos_list: list[dict], profile_text: str
) -> list["InputMediaPhoto"]:
    """
    Формирует медиа-группу с фотографиями пользователя.

    Args:
        photos_list (list[dict]): список фотографий пользователя.
        profile_text (str): текст профиля для caption первой фотографии.

    Returns:
        list[InputMediaPhoto]: список объектов InputMediaPhoto для медиа-группы.
    """
    from aiogram.types import InputMediaPhoto

    media_group = []
    for idx, photo_data in enumerate(photos_list):
        # Основная информация в caption только у первой фотографии
        caption = profile_text if idx == 0 else None
        media_group.append(
            InputMediaPhoto(
                media=photo_data["file_id"],
                caption=caption,
                parse_mode="HTML" if caption else None,
            )
        )
    return media_group
