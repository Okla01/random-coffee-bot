"""
Бизнес-логика онбординга для команды /start.

Модуль не отправляет сообщения в Telegram напрямую:
- принимает на вход пользователя, сессию БД и настройки;
- анализирует stage/status пользователя;
- при необходимости обновляет stage и фиксирует изменения в БД;
- возвращает StartResult с типом действия (action) и опциональным payload.

Хендлер /start на основе StartResult решает, какие сообщения/клавиатуры показывать.
"""

from dataclasses import dataclass
from typing import Literal, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core.config import Settings          # настройки приложения
from app.database.models import User              # ORM-модель пользователя
from app.services.core.text import contains_banned_words  # фильтр запрещённых слов


# Перечень возможных действий после обработки /start.
# Каждое значение — это "сигнал" для хендлера, что именно нужно сделать.
ActionType = Literal[
    "blocked",             # пользователь заблокирован
    "ask_email",           # запросить корпоративный e-mail
    "ask_code",            # запросить код подтверждения
    "ask_profile_name",    # запросить имя (или показать предзаполненное)
    "ask_profile_photo",   # запросить/показать фото профиля
    "ask_profile_bio",     # запросить текст "о себе"
    "ask_profile_age",     # запросить возраст
    "ask_profile_interests",  # запросить интересы
    "show_profile_review",    # показать предпросмотр анкеты перед подтверждением
    "show_profile_filled",    # показать уже заполненную анкету
]


@dataclass
class StartResult:
    """
    Результат обработки команды /start.

    Attributes:
        action (ActionType): тип следующего шага для хендлера.
        payload (dict[str, Any] | None): дополнительные данные для хендлера,
            например:
            - prefilled_name: предзаполненное имя из импорта;
            - has_photos: флаг наличия загруженных фото и т.п.
    """
    action: ActionType
    payload: dict[str, Any] | None = None  # доп. данные, если нужны


async def process_start(
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> StartResult:
    """
    Основная бизнес-логика сценария /start.

    На основании текущего статуса (status) и стадии (stage) пользователя
    определяет, что делать дальше, обновляет stage при необходимости
    и возвращает "сигнал" для хендлера.

    Args:
        session (AsyncSession): активная сессия БД.
        user (User): ORM-модель пользователя.
        settings (Settings): настройки приложения.

    Returns:
        StartResult: объект с действием (action) и опциональными данными (payload).
    """
    # 1. Пользователь заблокирован — никакой онбординг не запускаем
    if user.status == "blocked":
        return StartResult(action="blocked")

    # 2. Регистрация по почте:
    #    - "new"                 — только что созданный пользователь
    #    - "verifying_email"     — вводит/подтверждает почту
    #    - "verifying_email_error" — была ошибка, но остаёмся на этом шаге
    if user.stage in {"new", "verifying_email", "verifying_email_error"}:
        user.stage = "verifying_email"
        await session.commit()
        return StartResult(action="ask_email")

    # 3. Ожидание кода подтверждения:
    #    - "verifying_code"        — ждём ввода OTP-кода
    #    - "verifying_code_error"  — была ошибка, но стадия та же
    if user.stage in {"verifying_code", "verifying_code_error"}:
        await session.commit()
        return StartResult(action="ask_code")

    # 4. Пользователь авторизован — начинаем (или продолжаем) анкету с имени
    if user.stage == "authorized":
        # Переводим на первый шаг анкеты
        user.stage = "profile_name"

        # Попытка предзаполнения имени из импорта
        prefilled = None
        if user.origin == "import" and user.import_payload:
            prefilled = user.import_payload.get("profile_name")
            # Базовая валидация длины имени
            if prefilled and 2 <= len(prefilled) <= 100:
                # Проверка на запрещённые слова
                banned, _ = contains_banned_words(prefilled, settings.banned_words)
                if not banned:
                    # Фиксируем стадию и возвращаем действие с payload
                    await session.commit()
                    return StartResult(
                        action="ask_profile_name",
                        payload={"prefilled_name": prefilled},
                    )

        # Если предзаполнения нет или оно не прошло фильтры
        await session.commit()
        return StartResult(action="ask_profile_name")

    # 5. Остальные стадии профиля обрабатываем "как есть":

    # Пользователь уже на шаге ввода имени — просто повторно спрашиваем
    if user.stage == "profile_name":
        await session.commit()
        return StartResult(action="ask_profile_name")

    # Шаг загрузки фото профиля
    if user.stage == "profile_photo":
        await session.commit()
        return StartResult(
            action="ask_profile_photo",
            payload={
                # has_photos = True, если в photos_json есть список "photos"
                "has_photos": bool(user.photos_json and user.photos_json.get("photos"))
            },
        )

    # Шаг текста "о себе"
    if user.stage == "profile_bio":
        await session.commit()
        return StartResult(action="ask_profile_bio")

    # Шаг ввода возраста
    if user.stage == "profile_age":
        await session.commit()
        return StartResult(action="ask_profile_age")

    # Шаг ввода интересов
    if user.stage == "profile_interests":
        await session.commit()
        return StartResult(action="ask_profile_interests")

    # Предпросмотр анкеты перед подтверждением
    if user.stage == "profile_review":
        await session.commit()
        return StartResult(action="show_profile_review")

    # Анкета уже полностью заполнена — показываем финальный вариант
    if user.stage == "profile_filled":
        await session.commit()
        return StartResult(action="show_profile_filled")

    # 6. Дефолтный сценарий (на случай неизвестной/пустой стадии):
    #    считаем, что нужно начать с подтверждения e-mail
    user.stage = "verifying_email"
    await session.commit()
    return StartResult(action="ask_email")
