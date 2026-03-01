"""
Inline-обработчик для поиска пользователей.

Реализует универсальный inline-поиск пользователей для администраторов с префиксом user:.
Тип поиска определяется автоматически по формату запроса: @username — поиск по username,
только цифры — поиск по Telegram ID, иначе — поиск по имени в анкете. Формирует результаты
в виде InlineQueryResultArticle с данными профиля пользователя. При выборе результата
отправляет медиа-группу с фотографиями (если есть) и текстом профиля, а также клавиатуру
с действиями для управления пользователем.
"""

from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineQuery,
    ChosenInlineResult,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.db import (
    get_user_by_id,
    is_user_blocked,
    search_users_by_name,
    search_users_by_telegram_id,
    search_users_by_username,
)
from app.database.models import User
from app.handlers.fsm import FSMDataKeys
from app.keyboards.kb_admin import kb_admin_user_actions
from app.keyboards.utils import clear_last_kb
from app.services.admin import is_admin
from app.services.admin.inline_search import (
    prepare_inline_search_result,
    prepare_user_profile_data,
)
from app.services.const import (
    IS_RESULT_KEY_DESCRIPTION,
    IS_RESULT_KEY_ID,
    IS_RESULT_KEY_MESSAGE_TEXT,
    IS_RESULT_KEY_TITLE,
    UPD_KEY_HAS_PHOTOS,
    UPD_KEY_PROFILE_TEXT,
)
from app.services.core import Settings
from app.services.photo import send_user_photos

logger = logging.getLogger(__name__)

router = Router()


def _detect_search_type(search_query: str) -> tuple[str, str]:
    """
    Определяет тип поиска по строке запроса.

    Args:
        search_query (str): строка поискового запроса.

    Returns:
        tuple[str, str]: кортеж из типа поиска ("username", "id", "name") и очищенного запроса.
    """
    if search_query.startswith("@"):
        return "username", search_query
    elif search_query.isdigit():
        return "id", search_query
    else:
        return "name", search_query


async def _get_search_results(
    session: AsyncSession,
    search_query: str,
    search_type: str,
) -> list[User]:
    """
    Выполняет поиск пользователей в зависимости от типа.

    Args:
        session (AsyncSession): сессия БД.
        search_query (str): строка поиска.
        search_type (str): тип поиска ("username", "id", "name").

    Returns:
        list[User]: список найденных пользователей (до 50).
    """
    if search_type == "username":
        return await search_users_by_username(session, search_query, 50)
    elif search_type == "id":
        return await search_users_by_telegram_id(session, search_query, 50)
    else:
        return await search_users_by_name(session, search_query, 50)


# ----------------------------- Обработчик inline-поиска ----------------------------- #


@router.inline_query(F.query.startswith("user:"))
async def inline_search_users(
    query: InlineQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Универсальный поиск пользователей через inline-режим.

    Args:
        query (InlineQuery): объект inline-запроса.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, query.from_user.id):
            await query.answer(results=[], cache_time=1, is_personal=True)
            return

        search_query = query.query[5:].strip()
        if not search_query:
            await query.answer(results=[], cache_time=1, is_personal=True)
            return

        search_type, clean_query = _detect_search_type(search_query)
        users = await _get_search_results(session, clean_query, search_type)

        results: list[InlineQueryResultArticle] = []
        for user in users:
            result_data = await prepare_inline_search_result(session, user, search_type)
            results.append(
                InlineQueryResultArticle(
                    id=result_data[IS_RESULT_KEY_ID],
                    title=result_data[IS_RESULT_KEY_TITLE],
                    description=result_data[IS_RESULT_KEY_DESCRIPTION],
                    input_message_content=InputTextMessageContent(
                        message_text=result_data[IS_RESULT_KEY_MESSAGE_TEXT],
                        parse_mode=None,
                    ),
                )
            )

        await query.answer(results=results, cache_time=1, is_personal=True)


# ----------------------------- Обработчик выбора результата inline-поиска ----------------------------- #


@router.chosen_inline_result()
async def chosen_inline_result_handler(
    chosen_result: ChosenInlineResult,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает выбор результата inline-поиска.

    Args:
        chosen_result (ChosenInlineResult): объект выбранного результата.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.
    """

    await clear_last_kb(state, chosen_result.from_user.id, chosen_result.bot)

    async with session_factory() as session:
        if not await is_admin(session, settings, chosen_result.from_user.id):
            return

        try:
            user_id = int(chosen_result.result_id)
        except ValueError:
            return

        user = await get_user_by_id(session, user_id)
        if not user:
            return

        # Подготавливаем данные профиля пользователя
        profile_data = await prepare_user_profile_data(session, user)
        profile_text = profile_data[UPD_KEY_PROFILE_TEXT]

        # Проверяем статус пользователя для формирования клавиатуры
        user_is_blocked = is_user_blocked(user)
        user_is_admin = await is_admin(session, settings, user.telegram_id)

        keyboard = kb_admin_user_actions(
            user_id=user.id,
            is_blocked=user_is_blocked,
            is_admin=user_is_admin,
        )

        if not profile_data[UPD_KEY_HAS_PHOTOS]:
            # Нет фото — отправляем только текст с клавиатурой
            try:
                message = await chosen_result.bot.send_message(
                    chat_id=chosen_result.from_user.id,
                    text=profile_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: message.message_id})
            except Exception:
                pass
            return

        # Отправляем фото с текстом профиля в caption
        # send_user_photos автоматически проверяет и обновляет file_id при необходимости
        try:
            await send_user_photos(
                chosen_result.bot,
                chosen_result.from_user.id,
                user,
                settings,
                caption=profile_text,
                parse_mode="HTML",
                session=session,  # Передаём session для сохранения обновлённых file_id
            )
        except Exception as e:
            logger.error(
                "Ошибка отправки фото пользователя %s в inline_search: %s",
                user.id, e
            )

        # Отправляем отдельное сообщение с клавиатурой действий
        try:
            message = await chosen_result.bot.send_message(
                chat_id=chosen_result.from_user.id,
                text="⚙️ <b>Действия с пользователем:</b>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: message.message_id})
        except Exception:
            pass
