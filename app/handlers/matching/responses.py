"""
Обработка callback-кнопок «Готов познакомиться» и «Пропустить».
"""

from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram_dialog import StartMode
from aiogram_dialog.api.protocols import BgManagerFactory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Match, User
from app.database.db import get_user_by_tg_id
from app.services.core.config import Settings
from app.services.matching.constants import (
    MATCH_STATUS_COMPLETED,
    MATCH_STATUS_PENDING_RESPONSE,
    MATCH_STATUS_SCHEDULED,
    MATCH_STATUS_SKIPPED,
    MATCH_STATUS_WAITING_CONFIRM,
    MATCH_STATUS_WAITING_SLOTS,
    MATCH_USER_RESPONSE_CONFIRM,
    MATCH_USER_RESPONSE_NONE,
    MATCH_USER_RESPONSE_READY,
    MATCH_USER_RESPONSE_SKIP,
)
from app.services.matching.messages import (
    notify_match_confirm_waiting,
    notify_match_ready,
    notify_match_reschedule_partner,
    notify_match_reschedule_prompt,
    notify_match_scheduled,
    notify_match_skip_partner,
    notify_match_skip_self,
    notify_waiting_partner_ready,
)
from app.services.matching.storage import (
    cleanup_inactive_match,
    get_match_with_relations,
    set_match_response,
)
from app.handlers.matching.slots import MatchSlotsDialogSG
from app.handlers.fsm import MeetingFeedbackStates, FSMDataKeys
from app.keyboards.kb_matching import kb_meeting_feedback, kb_complaint_cancel
from app.services.admin.complaints import submit_complaint

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("match_ready:"))
async def on_match_ready(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    dialog_bg_factory: BgManagerFactory,
) -> None:
    """
    Обрабатывает callback кнопки «Готов познакомиться».

    Обновляет ответ пользователя на "ready" и, если оба участника готовы,
    переводит матч в статус waiting_slots и отправляет календарь выбора времени.

    Args:
        cq (CallbackQuery): объект callback-запроса от Telegram.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.

    Returns:
        None: ничего не возвращает.
    """
    await cq.message.edit_reply_markup(reply_markup=None)

    match_id = int(cq.data.split(":")[1])
    async with session_factory() as session:
        match = await get_match_with_relations(session, match_id)
        if not match or match.status != MATCH_STATUS_PENDING_RESPONSE:
            await cq.answer("Матч уже недоступен", show_alert=True)
            return
        user = await _get_user(session, cq.from_user.id)
        if not user:
            await cq.answer("Обновите регистрацию", show_alert=True)
            return

        updated = await set_match_response(
            session,
            match,
            user,
            MATCH_USER_RESPONSE_READY,
        )
        if not updated:
            await cq.answer("Эта кнопка недоступна для вас", show_alert=True)
            return

        both_ready = (
            match.user_a_response == MATCH_USER_RESPONSE_READY
            and match.user_b_response == MATCH_USER_RESPONSE_READY
        )
        if not both_ready:
            await notify_match_ready(cq.bot, match, user)
        if both_ready:
            match.status = MATCH_STATUS_WAITING_SLOTS
            match.last_reminder_at = None
        await session.commit()
        if both_ready:
            await _start_slots_dialog_for_user(
                cq.bot, dialog_bg_factory, match, match.user_a
            )
            await _start_slots_dialog_for_user(
                cq.bot, dialog_bg_factory, match, match.user_b
            )
            await notify_waiting_partner_ready(cq.bot, match)


@router.callback_query(F.data.startswith("match_skip:"))
async def on_match_skip(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await cq.message.edit_reply_markup(reply_markup=None)

    """
    Обрабатывает callback кнопки «Пропустить на этой неделе».

    Обновляет ответ пользователя на "skip", переводит матч в статус skipped
    и уведомляет обоих участников.

    Args:
        cq (CallbackQuery): объект callback-запроса от Telegram.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.

    Returns:
        None: ничего не возвращает.
    """
    match_id = int(cq.data.split(":")[1])
    async with session_factory() as session:
        match = await get_match_with_relations(session, match_id)
        if not match or match.status != MATCH_STATUS_PENDING_RESPONSE:
            await cq.answer("Матч уже недоступен", show_alert=True)
            return
        user = await _get_user(session, cq.from_user.id)
        if not user:
            await cq.answer("Обновите регистрацию", show_alert=True)
            return

        updated = await set_match_response(
            session,
            match,
            user,
            MATCH_USER_RESPONSE_SKIP,
        )
        if not updated:
            await cq.answer("Эта кнопка недоступна для вас", show_alert=True)
            return

        match.status = MATCH_STATUS_SKIPPED
        match.last_reminder_at = None
        # Очищаем данные неактивного матча
        await cleanup_inactive_match(session, match)
        await session.commit()

        await notify_match_skip_self(cq.bot, user)
        await notify_match_skip_partner(cq.bot, match, user)


@router.callback_query(F.data.startswith("match_confirm:"))
async def on_match_confirm(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback кнопки «Подтвердить» на этапе waiting_confirm.

    Обновляет ответ пользователя на "confirm" и, если оба участника подтвердили,
    переводит матч в статус scheduled и уведомляет обоих участников.

    Args:
        cq (CallbackQuery): объект callback-запроса от Telegram.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.

    Returns:
        None: ничего не возвращает.
    """
    await cq.message.delete_reply_markup()

    match_id = int(cq.data.split(":")[1])
    async with session_factory() as session:
        match = await get_match_with_relations(session, match_id)
        if (
            not match
            or match.status != MATCH_STATUS_WAITING_CONFIRM
            or not match.meeting_start_at
        ):
            await cq.answer("Матч уже недоступен", show_alert=True)
            return
        user = await _get_user(session, cq.from_user.id)
        if not user:
            await cq.answer("Обновите регистрацию", show_alert=True)
            return

        updated = await set_match_response(
            session,
            match,
            user,
            MATCH_USER_RESPONSE_CONFIRM,
        )
        if not updated:
            await cq.answer("Эта кнопка недоступна для вас", show_alert=True)
            return

        both_confirmed = (
            match.user_a_response == MATCH_USER_RESPONSE_CONFIRM
            and match.user_b_response == MATCH_USER_RESPONSE_CONFIRM
        )
        if both_confirmed:
            match.status = MATCH_STATUS_SCHEDULED
            match.last_reminder_at = None
            # Очищаем слоты и message_id после подтверждения встречи
            await cleanup_inactive_match(session, match)

        await session.commit()

    if both_confirmed:
        await notify_match_scheduled(cq.bot, match)
    else:
        await notify_match_confirm_waiting(cq.bot, user)


@router.callback_query(F.data.startswith("match_reschedule:"))
async def on_match_reschedule(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    dialog_bg_factory: BgManagerFactory,
) -> None:
    await cq.message.delete_reply_markup()

    """
    Обрабатывает callback кнопки «Назначить заново» на этапе waiting_confirm.

    Сбрасывает выбранное время встречи, очищает все слоты, переводит матч
    обратно в статус waiting_slots и отправляет календарь для повторного выбора.

    Args:
        cq (CallbackQuery): объект callback-запроса от Telegram.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.

    Returns:
        None: ничего не возвращает.
    """
    match_id = int(cq.data.split(":")[1])
    async with session_factory() as session:
        match = await get_match_with_relations(session, match_id)
        if not match or match.status != MATCH_STATUS_WAITING_CONFIRM:
            await cq.answer("Матч уже недоступен", show_alert=True)
            return
        user = await _get_user(session, cq.from_user.id)
        if not user:
            await cq.answer("Обновите регистрацию", show_alert=True)
            return

        match.status = MATCH_STATUS_WAITING_SLOTS
        match.meeting_start_at = None
        match.meeting_end_at = None
        match.user_a_response = MATCH_USER_RESPONSE_NONE
        match.user_b_response = MATCH_USER_RESPONSE_NONE
        match.last_reminder_at = None
        await _remove_last_message_keyboards(cq.bot, match)
        # Очищаем слоты и message_id при переназначении встречи
        await cleanup_inactive_match(session, match)
        await session.commit()

    # Сначала отправляем сообщение партнёру, затем открываем календари
    await notify_match_reschedule_partner(cq.bot, match, user)
    await _start_slots_dialog_for_user(cq.bot, dialog_bg_factory, match, match.user_a)
    await _start_slots_dialog_for_user(cq.bot, dialog_bg_factory, match, match.user_b)
    await notify_match_reschedule_prompt(cq.bot, match)
    await cq.answer("Выбор времени сброшен. Заполните новые слоты.", show_alert=False)


async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    """
    Получает пользователя по Telegram ID.

    Args:
        session (AsyncSession): активная сессия БД.
        telegram_id (int): Telegram ID пользователя.

    Returns:
        User | None: объект пользователя или None, если не найден.
    """
    return await get_user_by_tg_id(session, telegram_id)


async def _remove_last_message_keyboards(bot, match) -> None:
    """
    Удаляет inline-клавиатуры по сохранённым message_id у обоих участников.
    """
    for user, message_id_attr in (
        (match.user_a, "last_message_id_a"),
        (match.user_b, "last_message_id_b"),
    ):
        message_id = getattr(match, message_id_attr, None)
        if not user or not user.telegram_id or not message_id:
            continue
        try:
            await bot.edit_message_reply_markup(
                chat_id=user.telegram_id,
                message_id=message_id,
                reply_markup=None,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                logger.exception(
                    "Failed to remove keyboard for user %s message %s: %s",
                    getattr(user, "id", None),
                    message_id,
                    exc,
                )
        except Exception:
            logger.exception(
                "Failed to remove keyboard for user %s message %s",
                getattr(user, "id", None),
                message_id,
            )


async def _start_slots_dialog_for_user(
    bot: Bot,
    bg_factory: BgManagerFactory,
    match: Match,
    user: User | None,
) -> None:
    """
    Запускает диалог выбора слотов для указанного пользователя.
    """
    if not user or not user.telegram_id:
        return
    manager = bg_factory.bg(
        bot=bot,
        user_id=user.telegram_id,
        chat_id=user.telegram_id,
    )
    await manager.start(
        MatchSlotsDialogSG.calendar,
        data={"match_id": match.id, "user_id": user.id},
        mode=StartMode.RESET_STACK,
    )


@router.callback_query(F.data.startswith("meeting_complaint:"))
async def on_meeting_complaint(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает нажатие кнопки [⚠️] для подачи жалобы на встречу.

    Редактирует сообщение, запрашивая текст жалобы.
    """
    match_id = int(cq.data.split(":")[1])

    async with session_factory() as session:
        match = await get_match_with_relations(session, match_id)
        if not match or match.status != MATCH_STATUS_COMPLETED:
            await cq.answer("Встреча недоступна", show_alert=True)
            return

        user = await _get_user(session, cq.from_user.id)
        if not user:
            await cq.answer("Обновите регистрацию", show_alert=True)
            return

        # Определяем партнёра
        partner = match.user_b if user.id == match.user_a_id else match.user_a
        if not partner:
            await cq.answer("Партнёр не найден", show_alert=True)
            return

    # Сохраняем данные в FSM
    await state.update_data(
        **{
            FSMDataKeys.MEETING_FEEDBACK_MESSAGE_ID: cq.message.message_id,
            FSMDataKeys.MEETING_FEEDBACK_MATCH_ID: match_id,
            FSMDataKeys.MEETING_FEEDBACK_PARTNER_ID: partner.telegram_id,
        }
    )

    # Редактируем сообщение
    text = "Введите текст жалобы:"
    markup = kb_complaint_cancel(match_id)

    try:
        await cq.message.edit_text(text, reply_markup=markup)
        await state.set_state(MeetingFeedbackStates.waiting_complaint_text)
        await cq.answer()
    except Exception as e:
        logger.exception("Failed to edit message for complaint: %s", e)
        await cq.answer("Ошибка при обработке запроса", show_alert=True)


@router.callback_query(F.data.startswith("complaint_cancel:"))
async def on_complaint_cancel(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает отмену жалобы, возвращая к исходному сообщению с кнопками оценки.
    """
    match_id = int(cq.data.split(":")[1])

    async with session_factory() as session:
        match = await get_match_with_relations(session, match_id)
        if not match or match.status != MATCH_STATUS_COMPLETED:
            await cq.answer("Встреча недоступна", show_alert=True)
            return

    # Возвращаем исходное сообщение
    text = (
        "👋 Время вашей встречи наступило. "
        "Хорошего общения!\n"
        "После встречи вы можете оценить как всё прошло!"
    )
    markup = kb_meeting_feedback(match_id)

    try:
        await cq.message.edit_text(text, reply_markup=markup)
        await state.set_state(None)
        await cq.answer()
    except Exception as e:
        logger.exception("Failed to edit message for cancel complaint: %s", e)
        await cq.answer("Ошибка при обработке запроса", show_alert=True)


@router.message(StateFilter(MeetingFeedbackStates.waiting_complaint_text))
async def on_complaint_text_input(
    msg: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает ввод текста жалобы и отправляет её в админ-чат.
    """
    data = await state.get_data()
    match_id = data.get(FSMDataKeys.MEETING_FEEDBACK_MATCH_ID)
    message_id = data.get(FSMDataKeys.MEETING_FEEDBACK_MESSAGE_ID)
    partner_telegram_id = data.get(FSMDataKeys.MEETING_FEEDBACK_PARTNER_ID)

    if not match_id or not message_id or not partner_telegram_id:
        await msg.answer("Ошибка: данные не найдены. Попробуйте снова.")
        await state.set_state(None)
        return

    complaint_text = msg.text.strip()
    if not complaint_text:
        await msg.answer("Пожалуйста, введите текст жалобы.")
        return

    if len(complaint_text) > 1000:
        await msg.answer("Текст жалобы слишком длинный. Максимум 1000 символов.")
        return

    async with session_factory() as session:
        match = await get_match_with_relations(session, match_id)
        if not match or match.status != MATCH_STATUS_COMPLETED:
            await msg.answer("Встреча недоступна")
            await state.set_state(None)
            return

        user = await _get_user(session, msg.from_user.id)
        if not user:
            await msg.answer("Обновите регистрацию")
            await state.set_state(None)
            return

        if not settings.admin_chat_id:
            logger.error("ADMIN_CHAT_ID not configured")
            await msg.answer("Ошибка конфигурации. Обратитесь к администратору.")
            await state.set_state(None)
            return

        try:
            # Отправляем жалобу
            await submit_complaint(
                session=session,
                bot=msg.bot,
                admin_chat_id=settings.admin_chat_id,
                reporter_user_id=user.telegram_id,
                reported_user_id=partner_telegram_id,
                complaint_text=complaint_text,
                meeting_start_at=match.meeting_start_at,
                match_id=match.id,
            )

            # Удаляем сообщение пользователя с текстом жалобы
            try:
                await msg.delete()
            except Exception as e:
                logger.exception("Failed to delete user message: %s", e)

            # Редактируем сообщение с подтверждением и текстом жалобы
            confirmation_text = (
                f"Ваша жалоба отправлена.\nТекст вашей жалобы: {complaint_text}\n"
            )
            await msg.answer("Вы автоматически участвуете в следующем подборе пары!")
            try:
                await msg.bot.edit_message_text(
                    chat_id=msg.chat.id,
                    message_id=message_id,
                    text=confirmation_text,
                    reply_markup=None,
                )
            except Exception as e:
                logger.exception("Failed to edit message after complaint: %s", e)
                await msg.answer(confirmation_text)

            await state.set_state(None)

        except Exception as e:
            logger.exception("Failed to submit complaint: %s", e)
            await msg.answer("Ошибка при отправке жалобы. Попробуйте позже.")


@router.callback_query(F.data.startswith("meeting_positive:"))
async def on_meeting_positive(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает нажатие кнопки [👍] для положительной оценки встречи.
    """
    match_id = int(cq.data.split(":")[1])

    async with session_factory() as session:
        match = await get_match_with_relations(session, match_id)
        if not match or match.status != MATCH_STATUS_COMPLETED:
            await cq.answer("Встреча недоступна", show_alert=True)
            return

    # Редактируем сообщение
    text = "Рады, что встреча прошла успешно! Вы автоматически участвуете в следующем подборе пары!"

    try:
        await cq.message.edit_text(text, reply_markup=None)
        await cq.answer()
    except Exception as e:
        logger.exception("Failed to edit message for positive feedback: %s", e)
        await cq.answer("Ошибка при обработке запроса", show_alert=True)
