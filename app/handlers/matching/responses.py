"""
Обработка callback-кнопок «Готов выпить кофе» и «Пропустить».
"""

from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import User
from app.database.db import get_user_by_tg_id
from app.database.utils import now_msk
from app.services.core.config import Settings
from app.services.matching.constants import (
    MATCH_STATUS_PENDING_RESPONSE,
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_SKIPPED,
    MATCH_USER_RESPONSE_CONFIRM,
    MATCH_USER_RESPONSE_SKIP,
)
from app.services.matching.messages import (
    notify_match_ready,
    notify_match_scheduled,
    notify_match_skip_partner,
    notify_match_skip_self,
)
from app.services.matching.storage import (
    cleanup_inactive_match,
    get_match_with_relations,
    set_match_response,
    set_match_feedback,
    check_and_complete_match,
)
from app.handlers.fsm import MeetingFeedbackStates, FSMDataKeys
from app.keyboards.kb_matching import kb_meeting_feedback, kb_complaint_cancel
from app.services.admin.complaints import submit_complaint

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("match_ready:"))
async def on_match_ready(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback кнопки «Готов выпить кофе».

    Обновляет ответ пользователя на "confirm" и, если оба участника готовы,
    переводит матч в статус matched.

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
            MATCH_USER_RESPONSE_CONFIRM,
        )
        if not updated:
            await cq.answer("Эта кнопка недоступна для вас", show_alert=True)
            return

        both_confirm = (
            match.user_a_response == MATCH_USER_RESPONSE_CONFIRM
            and match.user_b_response == MATCH_USER_RESPONSE_CONFIRM
        )
        if not both_confirm:
            await notify_match_ready(cq.bot, match, user)
        if both_confirm:
            match.status = MATCH_STATUS_MATCHED
            match.last_reminder_at = None
            now = now_msk()
            if match.user_a:
                match.user_a.last_match_at = now
            if match.user_b:
                match.user_b.last_match_at = now
        await session.commit()
        if both_confirm:
            await notify_match_scheduled(cq.bot, match)


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
        if not match or match.status != MATCH_STATUS_MATCHED:
            await cq.answer("Встреча недоступна", show_alert=True)
            return

        user = await _get_user(session, cq.from_user.id)
        if not user:
            await cq.answer("Обновите регистрацию", show_alert=True)
            return

        # Проверяем, что пользователь еще не дал обратную связь
        user_feedback = match.user_a_feedback if user.id == match.user_a_id else match.user_b_feedback
        if user_feedback:
            await cq.answer("Вы уже дали обратную связь по этой встрече", show_alert=True)
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
        if not match or match.status != MATCH_STATUS_MATCHED:
            await cq.answer("Встреча недоступна", show_alert=True)
            return

    # Возвращаем исходное сообщение
    text = "Оцените пожалуйста как прошла ваша встреча. Это очень важно для нас!"
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
        if not match or match.status != MATCH_STATUS_MATCHED:
            await msg.answer("Встреча недоступна")
            await state.set_state(None)
            return

        user = await _get_user(session, msg.from_user.id)
        if not user:
            await msg.answer("Обновите регистрацию")
            await state.set_state(None)
            return

        # Проверяем, что пользователь еще не дал обратную связь
        user_feedback = match.user_a_feedback if user.id == match.user_a_id else match.user_b_feedback
        if user_feedback:
            await msg.answer("Вы уже дали обратную связь по этой встрече")
            await state.set_state(None)
            return

        # Используем admin_chat_id_complaints, если задан, иначе fallback на admin_chat_id
        complaints_chat_id = settings.admin_chat_id_complaints or settings.admin_chat_id
        if not complaints_chat_id:
            logger.error("ADMIN_CHAT_ID_COMPLAINTS or ADMIN_CHAT_ID not configured")
            await msg.answer("Ошибка конфигурации. Обратитесь к администратору.")
            await state.set_state(None)
            return

        try:
            # Отправляем жалобу
            await submit_complaint(
                session=session,
                bot=msg.bot,
                admin_chat_id=complaints_chat_id,
                reporter_user_id=user.telegram_id,
                reported_user_id=partner_telegram_id,
                complaint_text=complaint_text,
                match_id=match.id,
            )

            # Удаляем сообщение пользователя с текстом жалобы
            try:
                await msg.delete()
            except Exception as e:
                logger.exception("Failed to delete user message: %s", e)

            # Устанавливаем обратную связь от пользователя
            updated = await set_match_feedback(session, match, user, "complaint")
            if not updated:
                await msg.answer("Ошибка: вы не являетесь участником этой встречи")
                await state.set_state(None)
                return

            # Проверяем, дали ли оба пользователя обратную связь
            await check_and_complete_match(session, match)
            await session.commit()

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
        if not match or match.status != MATCH_STATUS_MATCHED:
            await cq.answer("Встреча недоступна", show_alert=True)
            return

        user = await _get_user(session, cq.from_user.id)
        if not user:
            await cq.answer("Обновите регистрацию", show_alert=True)
            return

        # Проверяем, что пользователь еще не дал обратную связь
        user_feedback = match.user_a_feedback if user.id == match.user_a_id else match.user_b_feedback
        if user_feedback:
            await cq.answer("Вы уже дали обратную связь по этой встрече", show_alert=True)
            return

        # Устанавливаем обратную связь от пользователя
        updated = await set_match_feedback(session, match, user, "positive")
        if not updated:
            await cq.answer("Эта кнопка недоступна для вас", show_alert=True)
            return

        # Проверяем, дали ли оба пользователя обратную связь
        await check_and_complete_match(session, match)
        await session.commit()

    # Редактируем сообщение
    text = "Рады, что встреча прошла успешно! Вы автоматически участвуете в следующем подборе пары!"

    try:
        await cq.message.edit_text(text, reply_markup=None)
        await cq.answer()
    except Exception as e:
        logger.exception("Failed to edit message for positive feedback: %s", e)
        await cq.answer("Ошибка при обработке запроса", show_alert=True)
