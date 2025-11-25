"""
Обработчики для заполнения профиля пользователя (анкета).

Реализует сценарий последовательного заполнения анкеты: имя → описание → возраст → интересы → предпросмотр.
Поддерживает редактирование отдельных полей, валидацию по запрещённым словам и длине.
Стадии профиля имеют приоритет и перехватывают текстовые сообщения перед регистрацией.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.dispatcher.event.bases import SkipHandler

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import Settings
from app.profile.keyboards import (
    kb_profile_filled,
    kb_profile_review,
    kb_profile_photo,
)
from app.profile.utils import contains_banned_words, normalize_interests
from app.profile.preview import (
    _send_profile_preview,
    build_profile_preview_text
)
from app.core.users import (
    get_or_create_user,
    update_user_stage,
)
from app.core.keyboards import (
    clear_last_kb,
)
from app.core.text import send_photo_request

router = Router()


# ----------------------- prefilled data ---------------------- #


@router.callback_query(F.data == "prof:prefilled:keep")
async def cb_prefilled_keep(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает выбор «Оставить ✅» для предзаполненных данных.

    Сохраняет предзаполненное имя из импорта и переводит пользователя на следующий шаг (описание).

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    await cq.message.edit_reply_markup(reply_markup=None)
    async with session_factory() as session:
        user = await get_or_create_user(session, cq.from_user.id, cq.from_user.username)
        await update_user_stage(session, user, "profile_bio", state, {"last_kb_mid": None})
        if user.import_payload and user.import_payload.get("profile_name"):
            user.name = user.import_payload["profile_name"]
        await cq.message.answer("Расскажите о себе (до 500 символов):")
    await cq.answer()


@router.callback_query(F.data == "prof:prefilled:new")
async def cb_prefilled_new(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает выбор «Ввести новые данные ✏️» — отвергает предзаполненные данные.

    Переводит пользователя на шаг ввода имени, позволяя ему ввести свои данные
    вместо предзаполненных из импорта.

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    await cq.message.edit_reply_markup(reply_markup=None)
    async with session_factory() as session:
        user = await get_or_create_user(session, cq.from_user.id, cq.from_user.username)
        await update_user_stage(session, user, "profile_name", state, {"last_kb_mid": None})
        await cq.message.answer("Давайте заполним анкету! Как вас зовут?")
    await cq.answer()


# --------------------------- text steps ------------------------- #


@router.message(F.text & ~F.text.startswith("/"))
async def on_profile_text(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает текстовый ввод на стадиях заполнения анкеты.

    Основной обработчик для всех текстовых шагов анкеты (имя, описание, возраст, интересы).
    Валидирует input по длине и запрещённым словам. Поддерживает редактирование отдельных полей
    с возвратом в предпросмотр. На не-свои стадии выбрасывает SkipHandler.

    Args:
        message (Message): объект сообщения от пользователя.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)

        # обрабатываем только свои стадии — если не наша стадия, отменяем обработчик
        if user.stage not in {
            "profile_name",
            "profile_bio",
            "profile_age",
            "profile_interests",
        }:
            await session.commit()
            raise SkipHandler()

        # гасим предыдущие кнопки
        await clear_last_kb(state, message.chat.id, message.bot)

        user.last_activity = datetime.now(timezone.utc)
        text = (message.text or "").strip()

        if user.status == "blocked":
            await session.commit()
            await message.answer(
                "Доступ временно заблокирован. Свяжитесь с администратором."
            )
            return

        # NAME
        if user.stage == "profile_name":
            if not (2 <= len(text) <= 100):
                await message.answer(
                    "⚠️ Имя должно быть от 2 до 100 символов. Попробуйте ещё раз."
                )
                await session.commit()
                return
            bad, word = contains_banned_words(text, settings.banned_words)
            if bad:
                await message.answer(
                    f"⚠️ Имя содержит запрещённое слово «{word}». Введите другое."
                )
                await session.commit()
                return
            user.name = text
            # Если редактируется отдельное поле, вернуть в режим просмотра; иначе продолжить
            data = await state.get_data()
            editing = data.get("editing_field")
            if editing == "name":
                user.stage = "profile_review"
                await session.commit()
                # Очистить флаг редактирования и отправить предпросмотр
                await state.update_data(editing_field=None)
                await _send_profile_preview(
                    message.bot, message.chat.id, user, state, kb_profile_review()
                )
                return

            # Перейти на этап загрузки фото
            user.stage = "profile_photo"
            await session.commit()
            
            await send_photo_request(message, state, kb_profile_photo())
            return

        # BIO
        if user.stage == "profile_bio":
            if len(text) > 500:
                await message.answer("⚠️ Описание должно быть не длиннее 500 символов.")
                await session.commit()
                return
            bad, word = contains_banned_words(text, settings.banned_words)
            if bad:
                await message.answer(
                    f"⚠️ Текст содержит запрещённое слово «{word}». Исправьте, пожалуйста."
                )
                await session.commit()
                return
            user.bio = text
            data = await state.get_data()
            editing = data.get("editing_field")
            if editing == "bio":
                user.stage = "profile_review"
                await session.commit()
                await state.update_data(editing_field=None)
                await _send_profile_preview(
                    message.bot, message.chat.id, user, state, kb_profile_review()
                )
                return

            user.stage = "profile_age"
            await session.commit()
            await message.answer("Введите ваш возраст (18–50):")
            await state.update_data(last_kb_mid=None)
            return

        # AGE
        if user.stage == "profile_age":
            if not text.isdigit():
                await message.answer("⚠️ Возраст должен быть числом от 18 до 50.\nВведите ваш возраст (18–50):")
                await session.commit()
                return
            age = int(text)
            if not (18 <= age <= 50):
                await message.answer("⚠️ Возраст должен быть числом от 18 до 50.\nВведите ваш возраст (18–50):")
                await session.commit()
                return
            user.age = age
            data = await state.get_data()
            editing = data.get("editing_field")
            if editing == "age":
                user.stage = "profile_review"
                await session.commit()
                await state.update_data(editing_field=None)
                await _send_profile_preview(
                    message.bot, message.chat.id, user, state, kb_profile_review()
                )
                return

            user.stage = "profile_interests"
            await session.commit()
            await message.answer(
                "Перечислите интересы через запятую (например: Python, музыка, дизайн)."
            )
            await state.update_data(last_kb_mid=None)
            return

        # INTERESTS
        if user.stage == "profile_interests":
            interests, err = normalize_interests(text, settings.banned_words)
            if err:
                await message.answer("⚠️ " + err)
                await session.commit()
                return
            user.interests_json = {"interests": interests or []}
            data = await state.get_data()
            editing = data.get("editing_field")
            if editing == "interests":
                user.stage = "profile_review"
                await session.commit()
                await state.update_data(editing_field=None)
                await _send_profile_preview(
                    message.bot, message.chat.id, user, state, kb_profile_review()
                )
                return

            user.stage = "profile_review"
            await session.commit()
            # Отправить текстовый предпросмотр
            await _send_profile_preview(
                message.bot, message.chat.id, user, state, kb_profile_review()
            )
            return


# --------------------------- review / save ---------------------- #


@router.callback_query(F.data == "prof:save")
async def cb_prof_save(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Сохранить ✅» — финализирует анкету.

    Переводит пользователя на стадию profile_filled, отправляет подтверждающее сообщение
    с кнопками для редактирования или участия в подборе.

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    await cq.message.edit_text("Анкета сохранена! 🎉", reply_markup=kb_profile_filled())
    
    try:
        await state.update_data(last_kb_mid=cq.message.message_id)
    except Exception:
        pass
    async with session_factory() as session:
        user = await get_or_create_user(session, cq.from_user.id, cq.from_user.username)
        user.stage = "profile_filled"
        await session.commit()
        await cq.answer()


@router.callback_query(F.data == "prof:edit:review")
async def cb_prof_edit_review(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Изменить анкету ✏️» — возвращает в режим редактирования.

    Переводит пользователя на стадию profile_review и отправляет предпросмотр анкеты.

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        user = await get_or_create_user(session, cq.from_user.id, cq.from_user.username)
        user.stage = "profile_review"
        await session.commit()
        
        # Формируем новый текст предпросмотра
        preview_text = build_profile_preview_text(user)

        try:
            await cq.message.edit_text(preview_text, reply_markup=kb_profile_review())

            # если тебе дальше нужен last_kb_mid — обнови его:
            await state.update_data(last_kb_mid=cq.message.message_id)

        except Exception:
            # если редактирование не удалось (например, сообщение слишком старое),
            # можно сделать fallback — отправить новое:
            sent = await cq.message.answer(
                preview_text,
                reply_markup=kb_profile_review(),
            )
            await state.update_data(last_kb_mid=sent.message_id)

    await cq.answer()



@router.callback_query(
    (F.data == "prof:edit:name") |
    (F.data == "prof:edit:bio") |
    (F.data == "prof:edit:age") |
    (F.data == "prof:edit:interests")
)
async def cb_prof_edit_field(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопок редактирования отдельных полей анкеты.

    Переводит пользователя на соответствующий шаг редактирования (имя, описание, возраст, интересы)
    и устанавливает флаг editing_field для возврата в режим предпросмотра после ввода.

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    await cq.message.edit_reply_markup(reply_markup=None)
    field = cq.data.split(":", 2)[2]
    async with session_factory() as session:
        user = await get_or_create_user(session, cq.from_user.id, cq.from_user.username)
        if field == "name":
            await update_user_stage(session, user, "profile_name", state, {"editing_field": field, "last_kb_mid": None})
            await cq.message.answer("Давайте заполним анкету! Как вас зовут?")
        elif field == "bio":
            await update_user_stage(session, user, "profile_bio", state, {"editing_field": field, "last_kb_mid": None})
            await cq.message.answer("Расскажите о себе (до 500 символов):")
        elif field == "age":
            await update_user_stage(session, user, "profile_age", state, {"editing_field": field, "last_kb_mid": None})
            await cq.message.answer("Введите ваш возраст (18–50):")
        elif field == "interests":
            await update_user_stage(session, user, "profile_interests", state, {"editing_field": field, "last_kb_mid": None})
            await cq.message.answer("Перечислите интересы через запятую.")
        await cq.answer()


@router.callback_query(F.data == "prof:join")
async def cb_prof_join(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Участвовать в подборе 🥰» — подтверждает участие.

    Отправляет подтверждающее сообщение и гасит кнопки. В будущем здесь будет логика
    включения пользователя в алгоритм подбора пары.

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.answer(
        "Отлично! Вы будете участвовать в подборе, когда это станет доступно."
    )
    await state.update_data(last_kb_mid=None)
    await cq.answer()
