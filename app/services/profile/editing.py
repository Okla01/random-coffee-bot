"""
Бизнес-логика редактирования профиля пользователя.

Модуль не отправляет сообщения в Telegram напрямую:
- принимает на вход пользователя, сессию БД, текст и настройки;
- валидирует данные (длина, запрещённые слова);
- обновляет поля пользователя;
- определяет следующую стадию;
- возвращает результат с типом действия и опциональными данными.

Хендлеры на основе результата решают, какие сообщения/клавиатуры показывать.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core.config import Settings
from app.services.core.text import contains_banned_words
from app.database.models import User
from app.services.profile.utils import normalize_interests


# Типы результатов обработки полей профиля
FieldResultType = Literal[
    "validation_error",      # ошибка валидации (нужно показать сообщение об ошибке)
    "field_updated_continue",  # поле обновлено, переходим к следующему шагу
    "field_updated_review",    # поле обновлено, возвращаемся в предпросмотр
    "blocked",                 # пользователь заблокирован
]


@dataclass
class FieldResult:
    """
    Результат обработки поля профиля.

    Attributes:
        result_type (FieldResultType): тип результата.
        error_message (str | None): сообщение об ошибке, если result_type == "validation_error".
        next_stage (str | None): следующая стадия, если поле обновлено.
        is_editing (bool): флаг, что это редактирование отдельного поля.
    """
    result_type: FieldResultType
    error_message: str | None = None
    next_stage: str | None = None
    is_editing: bool = False


def _is_profile_complete(user: User) -> bool:
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
        user.name and
        user.bio and
        user.age and
        user.interests_json and
        user.photos_json and
        user.photos_json.get("photos")
    )


async def process_name_field(
    session: AsyncSession,
    user: User,
    text: str,
    settings: Settings,
    editing_field: str | None,
) -> FieldResult:
    """
    Обрабатывает ввод имени пользователя.

    Валидирует имя по длине и запрещённым словам, обновляет поле user.name,
    определяет следующую стадию (продолжение или возврат в предпросмотр).

    Args:
        session (AsyncSession): активная сессия БД.
        user (User): ORM-модель пользователя.
        text (str): введённый текст имени.
        settings (Settings): настройки приложения.
        editing_field (str | None): флаг редактирования ("name" или None).

    Returns:
        FieldResult: результат обработки с типом действия и опциональными данными.
    """
    if user.status == "blocked":
        await session.commit()
        return FieldResult(result_type="blocked")

    # Валидация длины
    if not (2 <= len(text) <= 100):
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Имя должно быть от 2 до 100 символов. Попробуйте ещё раз."
        )

    # Проверка, что имя содержит только буквы и пробелы
    # Разрешаем пробелы для составных имен (например, "Мария Иванова")
    if not all(c.isalpha() or c.isspace() for c in text) or not any(c.isalpha() for c in text):
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Имя должно содержать только буквы. Попробуйте ещё раз."
        )

    # Проверка запрещённых слов
    bad, word = contains_banned_words(text, settings.banned_words)
    if bad:
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message=f"⚠️ Имя содержит запрещённое слово «{word}». Введите другое."
        )

    # Обновление поля
    user.name = text
    user.last_activity = datetime.now(timezone.utc)

    # Определение следующей стадии
    # Если editing_field установлен ИЛИ профиль уже заполнен - это редактирование
    is_editing = editing_field == "name" or _is_profile_complete(user)
    if is_editing:
        user.stage = "profile_review"
        await session.commit()
        return FieldResult(
            result_type="field_updated_review",
            next_stage="profile_review",
            is_editing=True
        )

    # Переход на этап загрузки фото
    user.stage = "profile_photo"
    await session.commit()
    return FieldResult(
        result_type="field_updated_continue",
        next_stage="profile_photo",
        is_editing=False
    )


async def process_bio_field(
    session: AsyncSession,
    user: User,
    text: str,
    settings: Settings,
    editing_field: str | None,
) -> FieldResult:
    """
    Обрабатывает ввод описания (bio) пользователя.

    Валидирует описание по длине и запрещённым словам, обновляет поле user.bio,
    определяет следующую стадию.

    Args:
        session (AsyncSession): активная сессия БД.
        user (User): ORM-модель пользователя.
        text (str): введённый текст описания.
        settings (Settings): настройки приложения.
        editing_field (str | None): флаг редактирования ("bio" или None).

    Returns:
        FieldResult: результат обработки.
    """
    if user.status == "blocked":
        await session.commit()
        return FieldResult(result_type="blocked")

    # Валидация длины
    if len(text) > 500:
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Описание должно быть не длиннее 500 символов."
        )

    # Проверка запрещённых слов
    bad, word = contains_banned_words(text, settings.banned_words)
    if bad:
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message=f"⚠️ Текст содержит запрещённое слово «{word}». Исправьте, пожалуйста."
        )

    # Обновление поля
    user.bio = text
    user.last_activity = datetime.now(timezone.utc)

    # Определение следующей стадии
    # Если editing_field установлен ИЛИ профиль уже заполнен - это редактирование
    is_editing = editing_field == "bio" or _is_profile_complete(user)
    if is_editing:
        user.stage = "profile_review"
        await session.commit()
        return FieldResult(
            result_type="field_updated_review",
            next_stage="profile_review",
            is_editing=True
        )

    # Переход на следующий шаг
    user.stage = "profile_age"
    await session.commit()
    return FieldResult(
        result_type="field_updated_continue",
        next_stage="profile_age",
        is_editing=False
    )


async def process_age_field(
    session: AsyncSession,
    user: User,
    text: str,
    settings: Settings,
    editing_field: str | None,
) -> FieldResult:
    """
    Обрабатывает ввод возраста пользователя.

    Валидирует возраст (число от 16 до 50), обновляет поле user.age,
    определяет следующую стадию.

    Args:
        session (AsyncSession): активная сессия БД.
        user (User): ORM-модель пользователя.
        text (str): введённый текст возраста.
        settings (Settings): настройки приложения.
        editing_field (str | None): флаг редактирования ("age" или None).

    Returns:
        FieldResult: результат обработки.
    """
    if user.status == "blocked":
        await session.commit()
        return FieldResult(result_type="blocked")

    # Валидация: должно быть числом
    if not text.isdigit():
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Возраст должен быть числом от 16 до 50.\nВведите ваш возраст (16–50):"
        )

    age = int(text)

    # Валидация диапазона
    if not (16 <= age <= 50):
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Возраст должен быть числом от 16 до 50.\nВведите ваш возраст (16–50):"
        )

    # Обновление поля
    user.age = age
    user.last_activity = datetime.now(timezone.utc)

    # Определение следующей стадии
    # Если editing_field установлен ИЛИ профиль уже заполнен - это редактирование
    is_editing = editing_field == "age" or _is_profile_complete(user)
    if is_editing:
        user.stage = "profile_review"
        await session.commit()
        return FieldResult(
            result_type="field_updated_review",
            next_stage="profile_review",
            is_editing=True
        )

    # Переход на следующий шаг
    user.stage = "profile_interests"
    await session.commit()
    return FieldResult(
        result_type="field_updated_continue",
        next_stage="profile_interests",
        is_editing=False
    )


async def process_interests_field(
    session: AsyncSession,
    user: User,
    text: str,
    settings: Settings,
    editing_field: str | None,
) -> FieldResult:
    """
    Обрабатывает ввод интересов пользователя.

    Нормализует и валидирует интересы, обновляет поле user.interests_json,
    определяет следующую стадию.

    Args:
        session (AsyncSession): активная сессия БД.
        user (User): ORM-модель пользователя.
        text (str): введённый текст интересов.
        settings (Settings): настройки приложения.
        editing_field (str | None): флаг редактирования ("interests" или None).

    Returns:
        FieldResult: результат обработки.
    """
    if user.status == "blocked":
        await session.commit()
        return FieldResult(result_type="blocked")

    # Нормализация и валидация интересов
    interests, err = normalize_interests(text, settings.banned_words)
    if err:
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ " + err
        )

    # Обновление поля
    user.interests_json = {"interests": interests or []}
    user.last_activity = datetime.now(timezone.utc)

    # Определение следующей стадии
    # Если editing_field установлен ИЛИ профиль уже заполнен - это редактирование
    is_editing = editing_field == "interests" or _is_profile_complete(user)
    if is_editing:
        user.stage = "profile_review"
        await session.commit()
        return FieldResult(
            result_type="field_updated_review",
            next_stage="profile_review",
            is_editing=True
        )

    # Переход на предпросмотр
    user.stage = "profile_review"
    await session.commit()
    return FieldResult(
        result_type="field_updated_continue",
        next_stage="profile_review",
        is_editing=False
    )


async def process_prefilled_keep(
    session: AsyncSession,
    user: User,
) -> str:
    """
    Обрабатывает выбор «Оставить ✅» для предзаполненных данных.

    Сохраняет предзаполненное имя из импорта и возвращает следующую стадию.

    Args:
        session (AsyncSession): активная сессия БД.
        user (User): ORM-модель пользователя.

    Returns:
        str: следующая стадия ("profile_bio").
    """
    if user.import_payload and user.import_payload.get("profile_name"):
        user.name = user.import_payload["profile_name"]
    return "profile_bio"


async def process_prefilled_new(
    session: AsyncSession,
    user: User,
) -> str:
    """
    Обрабатывает выбор «Ввести новые данные ✏️» — отвергает предзаполненные данные.

    Возвращает стадию для ввода нового имени.

    Args:
        session (AsyncSession): активная сессия БД.
        user (User): ORM-модель пользователя.

    Returns:
        str: следующая стадия ("profile_name").
    """
    return "profile_name"


async def process_save_profile(
    session: AsyncSession,
    user: User,
) -> None:
    """
    Финализирует анкету — переводит пользователя на стадию profile_filled.

    Args:
        session (AsyncSession): активная сессия БД.
        user (User): ORM-модель пользователя.
    """
    user.stage = "profile_filled"
    user.last_activity = datetime.now(timezone.utc)
    await session.commit()


async def process_edit_review(
    session: AsyncSession,
    user: User,
) -> None:
    """
    Переводит пользователя в режим редактирования (стадия profile_review).

    Args:
        session (AsyncSession): активная сессия БД.
        user (User): ORM-модель пользователя.
    """
    user.stage = "profile_review"
    user.last_activity = datetime.now(timezone.utc)
    await session.commit()

