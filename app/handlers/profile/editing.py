"""
Обработчики для заполнения профиля пользователя (анкета).

Реализует сценарий последовательного заполнения анкеты: имя → описание → возраст → интересы → предпросмотр.
Поддерживает редактирование отдельных полей, валидацию по запрещённым словам и длине.
Стадии профиля имеют приоритет и перехватывают текстовые сообщения перед регистрацией.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.dispatcher.event.bases import SkipHandler

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.core import Settings
from app.services.const import USER_STATUS_ACTIVE, USER_STATUS_NOT_ACTIVE
from app.keyboards.kb_profile import (
    kb_profile_review,
    kb_profile_photo,
)
from app.keyboards.utils import clear_last_kb

from app.services.profile.preview import (
    send_profile_preview,
    build_profile_preview_text
)
from app.services.profile.editing import (
    process_name_field,
    process_bio_field,
    process_age_field,
    process_interests_field,
    process_save_profile,
    process_edit_review,
)
from app.services.profile.photo import send_photo_request

from app.database.db import (
    get_or_create_user,
    update_user_stage,
)
from app.handlers.fsm import FSMDataKeys


router = Router()


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
    # Проверяем, не открыта ли админ-панель - если да, пропускаем обработку
    # Это позволяет админам использовать админ-панель, даже если они на стадии заполнения профиля
    state_data = await state.get_data()
    if state_data.get(FSMDataKeys.ADMIN_PANEL_ACTIVE):
        raise SkipHandler()
    
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

        text = (message.text or "").strip()

        # Получаем флаг редактирования из состояния
        data = await state.get_data()
        editing_field = data.get(FSMDataKeys.EDITING_FIELD)

        # NAME
        if user.stage == "profile_name":
            result = await process_name_field(session, user, text, settings, editing_field)
            
            if result.result_type == "validation_error":
                await message.answer(result.error_message)
                return
            
            if result.result_type == "field_updated_review":
                await state.update_data(**{FSMDataKeys.EDITING_FIELD: None})
                await send_profile_preview(
                    message.bot, message.chat.id, user, state, kb_profile_review()
                )
                return
            
            if result.result_type == "field_updated_continue":
                await send_photo_request(message, state, kb_profile_photo())
                return

        # BIO
        if user.stage == "profile_bio":
            result = await process_bio_field(session, user, text, settings, editing_field)
            
            if result.result_type == "validation_error":
                await message.answer(result.error_message)
                return
            
            if result.result_type == "field_updated_review":
                await state.update_data(**{FSMDataKeys.EDITING_FIELD: None})
                await send_profile_preview(
                    message.bot, message.chat.id, user, state, kb_profile_review()
                )
                return
            
            if result.result_type == "field_updated_continue":
                await message.answer("Введите ваш возраст (16–50):")
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
                return

        # AGE
        if user.stage == "profile_age":
            result = await process_age_field(session, user, text, settings, editing_field)
            
            if result.result_type == "validation_error":
                await message.answer(result.error_message)
                return
            
            if result.result_type == "field_updated_review":
                await state.update_data(**{FSMDataKeys.EDITING_FIELD: None})
                await send_profile_preview(
                    message.bot, message.chat.id, user, state, kb_profile_review()
                )
                return
            
            if result.result_type == "field_updated_continue":
                await message.answer(
                    "Перечислите интересы через запятую (например: Python, музыка, дизайн)."
                )
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
                return

        # INTERESTS
        if user.stage == "profile_interests":
            result = await process_interests_field(session, user, text, settings, editing_field)
            
            if result.result_type == "validation_error":
                await message.answer(result.error_message)
                return
            
            if result.result_type == "field_updated_review":
                await state.update_data(**{FSMDataKeys.EDITING_FIELD: None})
                await send_profile_preview(
                    message.bot, message.chat.id, user, state, kb_profile_review()
                )
                return
            
            if result.result_type == "field_updated_continue":
                # Отправить текстовый предпросмотр
                await send_profile_preview(
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

    Переводит пользователя на стадию profile_filled и сразу вызывает функционал участия в подборе.

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
        await process_save_profile(session, user)
        user.status = USER_STATUS_ACTIVE
        await session.commit()
    
    # Сразу вызываем функционал участия в подборе
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.answer(
        "Отлично! Вы будете участвовать в подборе, когда это станет доступно."
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
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
        await process_edit_review(session, user)
        
        # Формируем новый текст предпросмотра
        preview_text = build_profile_preview_text(user)

        try:
            await cq.message.edit_text(preview_text, reply_markup=kb_profile_review())

            # если тебе дальше нужен last_kb_mid — обнови его:
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})

        except Exception:
            # если редактирование не удалось (например, сообщение слишком старое),
            # можно сделать fallback — отправить новое:
            sent = await cq.message.answer(
                preview_text,
                reply_markup=kb_profile_review(),
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})

    await cq.answer()


@router.callback_query(
    (F.data == "prof:edit:name") |
    (F.data == "prof:edit:bio") |
    (F.data == "prof:edit:age") |
    (F.data == "prof:edit:interests") |
    (F.data == "prof:edit:timezone")
)
async def cb_prof_edit_field(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопок редактирования отдельных полей анкеты.

    Переводит пользователя на соответствующий шаг редактирования (имя, описание, возраст, интересы, часовой пояс)
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
        # При редактировании bio или interests устанавливаем статус not_active
        if field in {"bio", "interests"}:
            user.status = USER_STATUS_NOT_ACTIVE
        if field == "name":
            await update_user_stage(session, user, "profile_name", state, {FSMDataKeys.EDITING_FIELD: field, FSMDataKeys.LAST_KB_MID: None})
            await cq.message.answer("Давайте заполним анкету! Как вас зовут?")
        elif field == "bio":
            await update_user_stage(session, user, "profile_bio", state, {FSMDataKeys.EDITING_FIELD: field, FSMDataKeys.LAST_KB_MID: None})
            await cq.message.answer("Расскажите о себе (до 500 символов):")
        elif field == "age":
            await update_user_stage(session, user, "profile_age", state, {FSMDataKeys.EDITING_FIELD: field, FSMDataKeys.LAST_KB_MID: None})
            await cq.message.answer("Введите ваш возраст (16–50):")
        elif field == "interests":
            await update_user_stage(session, user, "profile_interests", state, {FSMDataKeys.EDITING_FIELD: field, FSMDataKeys.LAST_KB_MID: None})
            await cq.message.answer("Перечислите интересы через запятую.")
        await session.commit()
        await cq.answer()