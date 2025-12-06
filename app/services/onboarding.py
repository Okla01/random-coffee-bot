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

from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.handlers.fsm import FSMDataKeys
from app.handlers.profile.photo import send_photos_with_actions
from app.keyboards.kb_auth import kb_auth_code_wait
from app.keyboards.kb_profile import kb_profile_photo, kb_profile_review
from app.services.core.config import Settings  # настройки приложения
from app.database.models import User  # ORM-модель пользователя
from app.services.profile.preview import send_profile_preview
from app.services.matching.settings import get_setting_bool


# Перечень возможных действий после обработки /start.
# Каждое значение — это "сигнал" для хендлера, что именно нужно сделать.
ActionType = Literal[
    "ask_email",  # запросить корпоративный e-mail
    "ask_code",  # запросить код подтверждения
    "ask_profile_name",  # запросить имя (или показать предзаполненное)
    "wait_name_approval",  # ожидание одобрения заявки на доступ к анкете
    "ask_profile_photo",  # запросить/показать фото профиля
    "ask_profile_bio",  # запросить текст "о себе"
    "ask_profile_age",  # запросить возраст
    "ask_profile_interests",  # запросить интересы
    "show_profile_review",  # показать предпросмотр анкеты перед подтверждением
    "show_profile_filled",  # показать уже заполненную анкету
]


@dataclass
class StartResult:
    """
    Результат обработки команды /start.

    Attributes:
        action (ActionType): тип следующего шага для хендлера.
        payload (dict[str, Any] | None): дополнительные данные для хендлера,
            например:
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
    # Проверяем, включена ли авторизация по email
    try:
        email_auth_enabled = await get_setting_bool(session, "email_auth_enabled")
    except ValueError:
        # Если настройка отсутствует в БД, используем значение по умолчанию (True)
        email_auth_enabled = True

    # Если авторизация по email отключена, пропускаем её для новых пользователей
    if not email_auth_enabled:
        # Для новых пользователей или тех, кто на стадии авторизации по email
        if user.stage in {"new", "verifying_email", "verifying_email_error", "verifying_code", "verifying_code_error"}:
            user.stage = "profile_name"
            await session.commit()
            return StartResult(action="ask_profile_name")

    # 1. Регистрация по почте:
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
        await session.commit()
        return StartResult(action="ask_profile_name")

    # 5. Остальные стадии профиля обрабатываем "как есть":

    # Пользователь уже на шаге ввода имени — просто повторно спрашиваем
    if user.stage == "profile_name":
        await session.commit()
        return StartResult(action="ask_profile_name")

    # Пользователь ожидает одобрения заявки на доступ
    if user.stage == "profile_name_pending":
        await session.commit()
        return StartResult(action="wait_name_approval")

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


async def handle_start_result(
    message: Message,
    state: FSMContext,
    user: User,
    result: StartResult,
):
    """
    Обработчик результата process_start.
    Отправляет нужные сообщения и клавиатуры (UI-слой).
    """
    chat_id = message.chat.id
    bot = message.bot

    async def answer(text, **kwargs):
        return await message.answer(text, **kwargs)

    if result.action == "ask_email":
        await answer(
            "Привет! Давайте зарегистрируемся через корпоративную почту.\n"
            "Отправьте адрес (например, name@corp.com):"
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
        return

    if result.action == "ask_code":
        sent = await answer(
            "Мы уже отправили код подтверждения на вашу почту. Введите код.\n"
            "Если код истёк — воспользуйтесь кнопкой ниже.",
            reply_markup=kb_auth_code_wait(),
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
        return

    if result.action == "ask_profile_name":
        await answer(
            "Здравствуй! 👋\n\n"
            "Этот чат-бот поможет тебе найти коллег, которые скрасят твой обеденный перерыв приятной беседой☕️\n\n"
            "Так что давай заполним анкету!\n\n"
            "Напиши свое ФИО🙌"
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
        return

    if result.action == "wait_name_approval":
        await answer(
            "Отлично!💪\n\n"
            "Твоя заявка была отправлена на рассмотрение администратору! Пожалуйста, ожидай😌"
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
        return

    if result.action == "ask_profile_photo":
        has_photos = result.payload.get("has_photos") if result.payload else False
        if has_photos:
            photos_list = user.photos_json.get("photos", [])
            await send_photos_with_actions(bot, chat_id, user, state, photos_list)
        else:
            sent = await answer(
                "Добавь несколько своих фото(1-5шт)",
                reply_markup=kb_profile_photo(),
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
        return

    if result.action == "ask_profile_bio":
        await answer("А теперь кратко расскажи о себе самое интересное🔥\n(не более 500 символов)")
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
        return

    if result.action == "ask_profile_age":
        await answer("Укажи свой возраст (16–50):")
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
        return

    if result.action == "ask_profile_interests":
        await answer(
            "А теперь перечисли свои главные увлечения через запятую✍️\n"
            "(☝️Например: Python, музыка, дизайн)"
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
        return

    if result.action == "show_profile_review":
        await send_profile_preview(
            bot,
            chat_id,
            user,
            state,
            kb_profile_review(),
        )
        return

    if result.action == "show_profile_filled":
        await send_profile_preview(
            bot,
            chat_id,
            user,
            state,
            kb_profile_review(),
        )
        return
