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
    kb_profile_interests,
)
from app.keyboards.utils import clear_last_kb

from app.services.profile.preview import (
    send_profile_preview,
    build_profile_preview_text,
)
from app.services.const import (
    INTERESTS_PAGE_SIZE,
    MAX_INTERESTS_COUNT,
    MIN_INTERESTS_COUNT,
    UNIVERSAL_INTERESTS,
)
from app.services.profile.interests import (
    can_save_interests,
    clamp_page,
    normalize_selected_interests,
    toggle_interest,
    process_interests_field,
)
from app.services.profile.editing import (
    process_name_field,
    process_bio_field,
    process_age_field,
    process_save_profile,
    process_edit_review,
)
from app.services.profile.photo import send_photo_request

from app.database.db import (
    get_or_create_user,
    update_user_stage,
)
from app.handlers.fsm import FSMDataKeys
from aiogram.types import InputMediaPhoto
from app.keyboards.kb_admin import kb_admin_name_approval
from app.database import User


router = Router()


# --------------------------- helpers ------------------------- #


def _restore_selection(state_data: dict, user_interests_json: dict | None) -> list[str]:
    """
    Возвращает текущий выбор интересов из FSM или профиля.
    """
    from_state = state_data.get(FSMDataKeys.INTERESTS_SELECTED)
    if from_state is not None:
        return normalize_selected_interests(from_state)

    raw_from_user = (user_interests_json or {}).get("interests") if user_interests_json else []
    return normalize_selected_interests(raw_from_user)


async def _send_interests_keyboard(
    message: Message,
    state: FSMContext,
    selection: list[str],
    page: int = 1,
    *,
    mention_editing: bool = False,
) -> None:
    """
    Отправляет (или переотправляет) клавиатуру выбора интересов и сохраняет состояние.
    """
    page = clamp_page(page, len(UNIVERSAL_INTERESTS), INTERESTS_PAGE_SIZE)
    prompt = "А теперь выбери свои главные увлечения ✍️\n\n(☝️нужно выбрать от 4 увлечений)"

    markup = kb_profile_interests(
        selection,
        page,
        per_page=INTERESTS_PAGE_SIZE,
        min_required=MIN_INTERESTS_COUNT,
        max_allowed=MAX_INTERESTS_COUNT,
    )
    sent = await message.answer(prompt, reply_markup=markup)
    await state.update_data(
        **{
            FSMDataKeys.INTERESTS_SELECTED: selection,
            FSMDataKeys.INTERESTS_PAGE: page,
            FSMDataKeys.LAST_KB_MID: sent.message_id,
        }
    )


async def _notify_admin_profile_request(bot, settings: Settings, user: User) -> None:
    """
    Отправляет в админ-чат заявку на анкету со всеми данными пользователя.
    """
    if not settings.admin_chat_id:
        return

    photos = (user.photos_json or {}).get("photos", []) if user.photos_json else []
    media = [
        InputMediaPhoto(media=photo.get("file_id"))
        for photo in photos
        if photo.get("file_id")
    ]
    header_prefix = (
        f"🔗: @{user.username}" if user.username else f"Telegram ID: {user.telegram_id}"
    )
    header = (
        "🙋‍♂️ Новая заявка на анкету\n"
        f"🆔: {user.telegram_id}\n"
        f"{header_prefix}"
    )
    preview_text = build_profile_preview_text(user)
    text = f"{header}\n\n{preview_text}"

    try:
        if media:
            await bot.send_media_group(settings.admin_chat_id, media=media)
        await bot.send_message(
            settings.admin_chat_id,
            text,
            reply_markup=kb_admin_name_approval(user.id),
        )
    except Exception:
        pass


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
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )

        # Если пользователь ожидает одобрения заявки, отвечаем сообщением об ожидании
        if user.stage in {"profile_name_pending", "profile_review_pending"}:
            await session.commit()
            await message.answer(
                "Отлично!💪\n\n"
                "Твоя заявка была отправлена на рассмотрение администратору!\n\nПожалуйста, ожидай😌"
            )
            return

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
            result = await process_name_field(
                session, user, text, settings, editing_field
            )

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
                # Переход на этап загрузки фото
                await send_photo_request(message, state, kb_profile_photo())
                return

        # BIO
        if user.stage == "profile_bio":
            result = await process_bio_field(
                session, user, text, settings, editing_field
            )

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
                await message.answer("Подскажи свой возраст?🙏")
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
                return

        # AGE
        if user.stage == "profile_age":
            result = await process_age_field(
                session, user, text, settings, editing_field
            )

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
                selection = normalize_selected_interests(
                    (user.interests_json or {}).get("interests") if user.interests_json else []
                )
                await _send_interests_keyboard(
                    message,
                    state,
                    selection,
                    page=1,
                    mention_editing=False,
                )
                return

        # INTERESTS
        if user.stage == "profile_interests":
            state_data = await state.get_data()
            selection = _restore_selection(state_data, user.interests_json)
            page = int(state_data.get(FSMDataKeys.INTERESTS_PAGE) or 1)
            await session.commit()
            await _send_interests_keyboard(
                message,
                state,
                selection,
                page=page,
                mention_editing=bool(editing_field),
            )
            return


# --------------------------- interests callbacks ------------------ #


@router.callback_query(F.data.startswith("prof:int:"))
async def on_interests_callback(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Управляет выбором интересов (пагинация, выбор, сохранение, очистка).
    """
    parts = cq.data.split(":")
    if len(parts) < 3:
        await cq.answer()
        return

    action = parts[2]

    async with session_factory() as session:
        user = await get_or_create_user(session, cq.from_user.id, cq.from_user.username)
        if user.stage != "profile_interests":
            await cq.answer("Этот шаг уже завершён. Нажми /start для обновления.", show_alert=True)
            return

        state_data = await state.get_data()
        selection = _restore_selection(state_data, user.interests_json)
        page = int(state_data.get(FSMDataKeys.INTERESTS_PAGE) or 1)

        if action == "page" and len(parts) >= 4:
            try:
                page = int(parts[3])
            except ValueError:
                await cq.answer("Некорректная страница", show_alert=True)
                return
            page = clamp_page(page, len(UNIVERSAL_INTERESTS), INTERESTS_PAGE_SIZE)
            await state.update_data(**{FSMDataKeys.INTERESTS_PAGE: page})
            await cq.message.edit_reply_markup(
                reply_markup=kb_profile_interests(
                    selection,
                    page,
                    per_page=INTERESTS_PAGE_SIZE,
                    min_required=MIN_INTERESTS_COUNT,
                    max_allowed=MAX_INTERESTS_COUNT,
                )
            )
            await cq.answer()
            return

        if action == "sel" and len(parts) >= 4:
            try:
                index = int(parts[3])
            except ValueError:
                await cq.answer("Некорректный выбор", show_alert=True)
                return
            if not (0 <= index < len(UNIVERSAL_INTERESTS)):
                await cq.answer("Интерес не найден", show_alert=True)
                return
            interest = UNIVERSAL_INTERESTS[index]
            selection, error = toggle_interest(selection, interest, MAX_INTERESTS_COUNT)
            if error:
                await cq.answer(error, show_alert=True)
                return
            await state.update_data(
                **{
                    FSMDataKeys.INTERESTS_SELECTED: selection,
                    FSMDataKeys.INTERESTS_PAGE: page,
                }
            )
            await cq.message.edit_reply_markup(
                reply_markup=kb_profile_interests(
                    selection,
                    page,
                    per_page=INTERESTS_PAGE_SIZE,
                    min_required=MIN_INTERESTS_COUNT,
                    max_allowed=MAX_INTERESTS_COUNT,
                )
            )
            await cq.answer()
            return

        if action == "clear":
            selection = []
            page = 1
            await state.update_data(
                **{
                    FSMDataKeys.INTERESTS_SELECTED: selection,
                    FSMDataKeys.INTERESTS_PAGE: page,
                }
            )
            await cq.message.edit_reply_markup(
                reply_markup=kb_profile_interests(
                    selection,
                    page,
                    per_page=INTERESTS_PAGE_SIZE,
                    min_required=MIN_INTERESTS_COUNT,
                    max_allowed=MAX_INTERESTS_COUNT,
                )
            )
            await cq.answer("Выбор очищен")
            return

        if action == "save":
            if not can_save_interests(selection):
                await cq.answer(
                    f"Нужно выбрать от {MIN_INTERESTS_COUNT} до {MAX_INTERESTS_COUNT} интересов.",
                    show_alert=True,
                )
                return

            editing_field = state_data.get(FSMDataKeys.EDITING_FIELD)
            result = await process_interests_field(
                session, user, selection, settings, editing_field
            )

            if result.result_type == "validation_error":
                await cq.answer(result.error_message or "Нужно выбрать интересы", show_alert=True)
                return

            await state.update_data(
                **{
                    FSMDataKeys.INTERESTS_SELECTED: selection,
                    FSMDataKeys.INTERESTS_PAGE: 1,
                    FSMDataKeys.EDITING_FIELD: None,
                    FSMDataKeys.LAST_KB_MID: None,
                }
            )

            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await send_profile_preview(
                cq.bot, cq.message.chat.id, user, state, kb_profile_review()
            )
            await cq.answer("Интересы сохранены")
            return

    await cq.answer()


# --------------------------- review / save ---------------------- #


@router.callback_query(F.data == "prof:save")
async def cb_prof_save(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Сохранить ✅».

    Формирует заявку в админ-чат на финальное одобрение анкеты и ставит профиль
    в статус ожидания.

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

        if user.stage == "profile_review_pending":
            await cq.answer("Заявка уже на рассмотрении", show_alert=True)
            return

        # Если профиль уже был одобрен ранее, сразу финализируем без отправки заявки
        if user.profile_approved:
            await process_save_profile(session, user)
            user.status = USER_STATUS_ACTIVE
            await session.commit()

            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await cq.message.answer(
                "✨Вуаля✨\n"
                "Теперь ты автоматически участвуешь в следующем подборе друллеги!🤗"
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
            await cq.answer()
            return

        # Отправляем заявку
        await _notify_admin_profile_request(cq.bot, settings, user)

        # Ставим профиль в ожидание решения
        user.stage = "profile_review_pending"
        user.status = USER_STATUS_NOT_ACTIVE
        await session.commit()

    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await cq.message.answer(
        "Отлично!💪\n\n"
        "Твоя заявка была отправлена на рассмотрение администратору!\n\nПожалуйста, ожидай😌"
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
    (F.data == "prof:edit:name")
    | (F.data == "prof:edit:bio")
    | (F.data == "prof:edit:age")
    | (F.data == "prof:edit:interests")
    | (F.data == "prof:edit:timezone")
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
            await update_user_stage(
                session,
                user,
                "profile_name",
                state,
                {FSMDataKeys.EDITING_FIELD: field, FSMDataKeys.LAST_KB_MID: None},
            )
            await cq.message.answer(
                "Здравствуй! 👋\n\n"
                "Этот чат-бот поможет тебе найти коллег, которые скрасят твой обеденный перерыв приятной беседой☕️\n\n"
                "Давай заполним небольшую анкету!\n"
                "Напиши свое ФИО🙌"
            )
        elif field == "bio":
            await update_user_stage(
                session,
                user,
                "profile_bio",
                state,
                {FSMDataKeys.EDITING_FIELD: field, FSMDataKeys.LAST_KB_MID: None},
            )
            await cq.message.answer("Коротко расскажи о себе самое интересное🔥\n(не более 500 символов)")
        elif field == "age":
            await update_user_stage(
                session,
                user,
                "profile_age",
                state,
                {FSMDataKeys.EDITING_FIELD: field, FSMDataKeys.LAST_KB_MID: None},
            )
            await cq.message.answer("Подскажи свой возраст?🙏")
        elif field == "interests":
            await update_user_stage(
                session,
                user,
                "profile_interests",
                state,
                {
                    FSMDataKeys.EDITING_FIELD: field,
                    FSMDataKeys.LAST_KB_MID: None,
                    FSMDataKeys.INTERESTS_SELECTED: normalize_selected_interests(
                        (user.interests_json or {}).get("interests") if user.interests_json else []
                    ),
                    FSMDataKeys.INTERESTS_PAGE: 1,
                },
            )
            selection = _restore_selection(await state.get_data(), user.interests_json)
            await _send_interests_keyboard(
                cq.message,
                state,
                selection,
                page=1,
                mention_editing=True,
            )
        await session.commit()
        await cq.answer()
