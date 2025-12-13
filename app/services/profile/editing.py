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

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core.config import Settings
from app.services.profile.banned_words import contains_banned_words
from app.services.profile.utils import is_profile_complete
from app.services.profile.types import FieldResult
from app.keyboards.kb_admin import kb_admin_name_approval
from app.database.models import User, AdminLog


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
    # Валидация длины
    if not (2 <= len(text) <= 100):
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Имя должно быть от 2 до 100 символов. Попробуйте ещё раз.",
        )

    # Проверка, что имя содержит только буквы и пробелы
    # Разрешаем пробелы для составных имен (например, "Мария Иванова")
    if not all(c.isalpha() or c.isspace() for c in text) or not any(
        c.isalpha() for c in text
    ):
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Имя должно содержать только буквы. Попробуйте ещё раз.",
        )

    # Проверка запрещённых слов
    bad, word = contains_banned_words(text, settings.banned_words)
    if bad:
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message=f"⚠️ Имя содержит запрещённое слово «{word}». Введите другое.",
        )

    # Обновление поля
    user.name = text

    # Определение следующей стадии
    # Если editing_field установлен ИЛИ профиль уже заполнен - это редактирование
    is_editing = editing_field == "name" or is_profile_complete(user)
    if is_editing:
        user.stage = "profile_review"
        await session.commit()
        return FieldResult(
            result_type="field_updated_review",
            next_stage="profile_review",
            is_editing=True,
        )

    # Переход на этап ожидания одобрения заявки на доступ
    user.stage = "profile_name_pending"
    await session.commit()
    return FieldResult(
        result_type="field_updated_continue",
        next_stage="profile_name_pending",
        is_editing=False,
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
    # Валидация длины
    if len(text) > 500:
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Описание должно быть не длиннее 500 символов.",
        )

    # Проверка запрещённых слов
    bad, word = contains_banned_words(text, settings.banned_words)
    if bad:
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message=f"⚠️ Текст содержит запрещённое слово «{word}». Исправьте, пожалуйста.",
        )

    # Обновление поля
    user.bio = text

    # Определение следующей стадии
    # Если editing_field установлен ИЛИ профиль уже заполнен - это редактирование
    is_editing = editing_field == "bio" or is_profile_complete(user)
    if is_editing:
        user.stage = "profile_review"
        await session.commit()
        return FieldResult(
            result_type="field_updated_review",
            next_stage="profile_review",
            is_editing=True,
        )

    # Переход на следующий шаг
    user.stage = "profile_age"
    await session.commit()
    return FieldResult(
        result_type="field_updated_continue", next_stage="profile_age", is_editing=False
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
    # Валидация: должно быть числом
    if not text.isdigit():
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Возраст должен быть числом от 16 до 50.\nУкажи свой возраст (16–50):",
        )

    age = int(text)

    # Валидация диапазона
    if not (16 <= age <= 50):
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message="⚠️ Возраст должен быть числом от 16 до 50.\nУкажи свой возраст (16–50):",
        )

    # Обновление поля
    user.age = age

    # Определение следующей стадии
    # Если editing_field установлен ИЛИ профиль уже заполнен - это редактирование
    is_editing = editing_field == "age" or is_profile_complete(user)
    if is_editing:
        user.stage = "profile_review"
        await session.commit()
        return FieldResult(
            result_type="field_updated_review",
            next_stage="profile_review",
            is_editing=True,
        )

    # Переход на следующий шаг
    user.stage = "profile_interests"
    await session.commit()
    return FieldResult(
        result_type="field_updated_continue",
        next_stage="profile_interests",
        is_editing=False,
    )


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
    await session.commit()


async def notify_admin_on_name_request(
    session: AsyncSession,
    settings: Settings,
    user: User,
    bot,
) -> None:
    """
    Уведомляет администратора о заявке на доступ к анкетированию.

    Логирует событие в admin_log, затем отправляет сообщение в admin_chat_id
    с информацией о пользователе и кнопками для принятия решения (одобрить/отклонить).

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация (содержит admin_chat_id).
        user (User): объект пользователя.
        bot: объект бота для отправки сообщения.

    Returns:
        None: ничего не возвращает.
    """
    if not settings.admin_chat_id:
        return

    payload = {
        "user_id": user.id,
        "name": user.name,
    }
    session.add(
        AdminLog(
            admin_id=0,
            action="name_approval_request",
            payload=payload,
        )
    )
    await session.commit()

    # Отправляем сообщение в админ-чат с информацией о пользователе и кнопками для принятия решения
    try:
        text = (
            f"🙋‍♂️ Запрос на доступ к анкетированию\n"
            f"👤: {user.name}\n"
            f"🔗: {'@' + user.username if user.username else 'нет username'}\n"
            f"🆔: {user.telegram_id}"
        )
        await bot.send_message(
            chat_id=settings.admin_chat_id,
            text=text,
            reply_markup=kb_admin_name_approval(user.id),
        )
    except Exception:
        # Не фейлим основную операцию из-за ошибки отправки нотификации
        pass
