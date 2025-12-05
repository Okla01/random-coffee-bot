"""
Утилиты для работы с профилем и фотографиями.

Содержит функции для управления фото пользователя, валидации данных профиля.
"""

from __future__ import annotations
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from app.database import User


def is_profile_complete(user: User) -> bool:
    """
    Проверяет, заполнен ли профиль полностью.

    Профиль считается заполненным, если все обязательные поля заполнены:
    - имя (name)
    - описание (bio)
    - возраст (age)
    - интересы (interests_json)
    - фото (photos_json с непустым списком photos)

    Args:
        user (User): объект пользователя.

    Returns:
        bool: True если все обязательные поля заполнены, иначе False.
    """
    return bool(
        user.name
        and user.bio
        and user.age
        and user.interests_json
        and user.photos_json
        and user.photos_json.get("photos")
    )
