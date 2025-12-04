"""
Inline-обработчик для поиска пользователей.

Администраторы могут искать пользователей через inline-режим бота
с единым префиксом user: — тип поиска определяется автоматически:
- @username — поиск по username
- 123456789 (только цифры) — поиск по Telegram ID
- Имя — поиск по имени в анкете
"""

from __future__ import annotations

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
    build_media_group,
    prepare_inline_search_result,
    prepare_user_profile_data,
)
from app.services.const import (
    IS_RESULT_KEY_DESCRIPTION,
    IS_RESULT_KEY_ID,
    IS_RESULT_KEY_MESSAGE_TEXT,
    IS_RESULT_KEY_TITLE,
    UPD_KEY_HAS_PHOTOS,
    UPD_KEY_PHOTOS_LIST,
    UPD_KEY_PROFILE_TEXT,
)
from app.services.core import Settings

router = Router()


def _detect_search_type(search_query: str) -> tuple[str, str]:
    """
    Определяет тип поиска по строке запроса.

    Returns:
        tuple[str, str]: (тип поиска, очищенный запрос)
        Типы: "username", "id", "name"
    """
    if search_query.startswith("@"):
        # Поиск по username (убираем @)
        return "username", search_query
    elif search_query.isdigit():
        # Поиск по Telegram ID
        return "id", search_query
    else:
        # Поиск по имени в анкете
        return "name", search_query


async def _get_search_results(
    session: AsyncSession,
    search_query: str,
    search_type: str,
) -> list[User]:
    """
    Выполняет поиск пользователей в зависимости от типа.

    Args:
        session: сессия БД.
        search_query: строка поиска.
        search_type: тип поиска ("username", "id", "name").

    Returns:
        list[User]: список найденных пользователей.
    """
    # Telegram API ограничивает количество результатов до 50
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
    Универсальный поиск пользователей.
    
    Формат: user:<запрос>
    - @username — поиск по username
    - 123456789 — поиск по Telegram ID  
    - Имя — поиск по имени в анкете
    """
    async with session_factory() as session:
        # Проверка прав администратора
        if not await is_admin(session, settings, query.from_user.id):
            await query.answer(results=[], cache_time=1, is_personal=True)
            return

        # Извлечение и обработка поискового запроса
        search_query = query.query[5:].strip()
        if not search_query:
            await query.answer(results=[], cache_time=1, is_personal=True)
            return

        # Определение типа поиска
        search_type, clean_query = _detect_search_type(search_query)

        # Выполнение поиска
        users = await _get_search_results(session, clean_query, search_type)

        # Формирование результатов
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
    
    Отправляет медиа-группу с фотографиями пользователя и текстом профиля,
    если у пользователя есть фотографии.
    """

    await clear_last_kb(state, chosen_result.from_user.id, chosen_result.bot)

    # Проверка прав администратора
    async with session_factory() as session:
        if not await is_admin(session, settings, chosen_result.from_user.id):
            return
        
        # Получаем ID пользователя из результата
        try:
            user_id = int(chosen_result.result_id)
        except ValueError:
            return
        
        # Получаем пользователя из БД по ID
        user = await get_user_by_id(session, user_id)
        if not user:
            return
        
        # Подготавливаем данные профиля пользователя
        profile_data = await prepare_user_profile_data(session, user)
        profile_text = profile_data[UPD_KEY_PROFILE_TEXT]
        photos_list = profile_data[UPD_KEY_PHOTOS_LIST]
        
        # Проверяем статус пользователя для формирования клавиатуры
        user_is_blocked = is_user_blocked(user)
        user_is_admin = await is_admin(session, settings, user.telegram_id)
        
        # Формируем клавиатуру действий
        keyboard = kb_admin_user_actions(
            user_id=user.id,
            is_blocked=user_is_blocked,
            is_admin=user_is_admin,
        )
        
        if not profile_data[UPD_KEY_HAS_PHOTOS]:
            # Если нет фотографий, отправляем полный текст профиля с клавиатурой
            try:
                message = await chosen_result.bot.send_message(
                    chat_id=chosen_result.from_user.id,
                    text=profile_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                # Сохраняем message_id последней клавиатуры в FSM
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: message.message_id})
            except Exception:
                pass
            return
        
        # Формируем и отправляем медиа-группу с фотографиями
        media_group = build_media_group(photos_list, profile_text)
        
        # Отправляем медиа-группу
        try:
            await chosen_result.bot.send_media_group(
                chat_id=chosen_result.from_user.id,
                media=media_group,
            )
        except Exception:
            pass
        
        # Отправляем отдельное сообщение с клавиатурой действий
        # (Telegram API не позволяет редактировать клавиатуру в медиа-группах)
        try:
            message = await chosen_result.bot.send_message(
                chat_id=chosen_result.from_user.id,
                text="⚙️ <b>Действия с пользователем:</b>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            # Сохраняем message_id последней клавиатуры в FSM
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: message.message_id})
        except Exception:
            pass
