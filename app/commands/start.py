"""
Обработчик команды /start для инициализации и восстановления сценария.

Создаёт нового пользователя при первом входе и восстанавливает текущую стадию
с последнего сеанса. Гасит старые клавиатуры и выводит соответствующее сообщение
в зависимости от статуса пользователя (заблокирован, новый, авторизован, анкета заполнена).
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import Settings, setup_logging
from app.profile.keyboards import kb_profile_filled, kb_profile_photo, kb_prefilled_data, kb_profile_review
from app.auth.keyboards import kb_auth_code_wait
from app.profile.preview import _send_profile_preview
from app.core.users import get_or_create_user
from app.core.keyboards import clear_last_kb
from app.core.text import contains_banned_words
from app.profile.photo import _send_photos_with_actions


router = Router()
setup_logging()


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает команду /start для инициализации/восстановления пользователя.

    Создаёт пользователя при первом входе, восстанавливает его текущую стадию
    и выводит соответствующее сообщение. Проверяет статус блокировки,
    восстанавливает незавершённые сценарии (регистрация, анкета).

    Args:
        message (Message): объект сообщения /start.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )

        # гасим старые кнопки, если есть
        await clear_last_kb(state, message.chat.id, message.bot)

        if user.status == "blocked":
            await session.commit()
            await message.answer(
                "Доступ временно заблокирован. Свяжитесь с администратором."
            )
            return

        # регистрация e-mail
        if user.stage in {"new", "verifying_email", "verifying_email_error"}:
            user.stage = "verifying_email"
            await session.commit()
            await message.answer(
                "Привет! Давайте зарегистрируемся через корпоративную почту.\n"
                "Отправьте адрес (например, name@corp.com):"
            )
            return

        # ожидание OTP — показываем кнопки переотправки/смены почты
        if user.stage in {"verifying_code", "verifying_code_error"}:
            await session.commit()
            sent = await message.answer(
                "Мы уже отправили код подтверждения на вашу почту. Введите код.\n"
                "Если код истёк — воспользуйтесь кнопкой ниже.",
                reply_markup=kb_auth_code_wait(),
            )
            await state.update_data(last_kb_mid=sent.message_id)
            return

        # авторизован — начинаем с профиля
        if user.stage == "authorized":
            await session.commit()
            # Переводим на первый этап профиля - имя
            user.stage = "profile_name"
            
            # Предзаполнение имени из import_payload
            prefilled = None
            if user.origin == "import" and user.import_payload:
                prefilled = user.import_payload.get("profile_name")
                if prefilled and 2 <= len(prefilled) <= 100:
                    banned, _ = contains_banned_words(prefilled, settings.banned_words)
                    if not banned:
                        await session.commit()
                        sent = await message.answer(
                            f"У нас есть ваше имя из импорта: {prefilled}\nОставить или ввести новое?",
                            reply_markup=kb_prefilled_data(),
                        )
                        await state.update_data(last_kb_mid=sent.message_id)
                        return

            await session.commit()
            await message.answer("Давайте заполним анкету! Как вас зовут?")
            await state.update_data(last_kb_mid=None)
            return

        # PROFILE_NAME
        if user.stage == "profile_name":
            await session.commit()
            await message.answer("Давайте заполним анкету! Как вас зовут?")
            await state.update_data(last_kb_mid=None)
            return

        # загрузка фото профиля
        if user.stage == "profile_photo":
            await session.commit()
            if user.photos_json and user.photos_json.get("photos"):
                # Если уже есть фото, показываем их с кнопками
                photos_list = user.photos_json.get("photos", [])
                await _send_photos_with_actions(
                    message.bot, message.chat.id, user, state, photos_list
                )
            else:
                # Первый раз — просим добавить фото
                sent = await message.answer(
                    "Пришлите пожалуйста фото для анкеты (от 1 до 3 фото), "
                    "либо используйте текущее фото вашего профиля.",
                    reply_markup=kb_profile_photo(),
                )
                await state.update_data(last_kb_mid=sent.message_id)
            return

        # PROFILE_BIO
        if user.stage == "profile_bio":
            await session.commit()
            await message.answer("Расскажите о себе (до 500 символов):")
            await state.update_data(last_kb_mid=None)
            return

        # PROFILE_AGE
        if user.stage == "profile_age":
            await session.commit()
            await message.answer("Введите ваш возраст (18–50):")
            await state.update_data(last_kb_mid=None)
            return

        # PROFILE_INTERESTS
        if user.stage == "profile_interests":
            await session.commit()
            await message.answer(
                "Перечислите интересы через запятую (например: Python, музыка, дизайн)."
            )
            await state.update_data(last_kb_mid=None)
            return

        # PROFILE_REVIEW
        if user.stage == "profile_review":
            await session.commit()
            await _send_profile_preview(
                message.bot, message.chat.id, user, state, kb_profile_review()
            )
            return

        if user.stage == "profile_filled":
            # Отправить текстовый предпросмотр, затем текст и кнопки
            user.stage = "profile_filled"  # Гарантировать, что стадия установлена
            await session.commit()
            await _send_profile_preview(
                message.bot, message.chat.id, user, state, kb_profile_filled()
            )
            return

        await session.commit()
        await message.answer("Начнём регистрацию. Отправьте ваш корпоративный e-mail.")
