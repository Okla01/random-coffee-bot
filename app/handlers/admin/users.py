"""
Обработчик команды кнопки "Пользователи" и всех callback-запросов для просмотра списка пользователей.

Обрабатывает callback-запросы для просмотра списка пользователей с пагинацией, переключения
фильтров (активные/заблокированные пользователи) и навигации по страницам. Форматирует текст
со списком пользователей, отображает клавиатуру с фильтрами и навигацией по страницам.
Поддерживает сохранение состояния фильтров в FSM для корректной работы пагинации.
"""

from __future__ import annotations

from aiogram import Router, F

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.handlers.fsm import FSMDataKeys
from app.keyboards.kb_admin import kb_admin_users
from app.keyboards.utils import clear_last_kb
from app.services.admin.users import build_users_page_text, get_users_page
from app.services.const import USERS_PER_PAGE

router = Router()


@router.callback_query(F.data == "admin:users")
async def cb_admin_users(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает запрос на просмотр списка пользователей.

    Удаляет предыдущую клавиатуру, устанавливает фильтры по умолчанию (все пользователи),
    получает первую страницу пользователей из базы данных, форматирует текст со списком
    и отображает меню со списком пользователей с клавиатурой фильтров и навигации.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.

    Returns:
        None: ничего не возвращает.
    """

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)
    # Установка фильтров по умолчанию
    await state.update_data(filters={"active": False, "blocked": False})

    async with session_factory() as session:
        data = await state.get_data()
        filters = data["filters"]

        users, total_users = await get_users_page(
            session, page=1, per_page=USERS_PER_PAGE, filters=filters
        )

        text = await build_users_page_text(session, users)
        # Редактируем текущее сообщение
        await cq.message.edit_text(
            text,
            reply_markup=kb_admin_users(
                page=1, total_users=total_users, filters=filters
            ),
        )
        # Сохранение ID последней отправленной клавиатуры
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


@router.callback_query(F.data.startswith("admin:users:filter:"))
async def cb_admin_users_filter(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback-запросы для переключения фильтров списка пользователей.

    Извлекает тип фильтра (active/blocked) из callback data, получает текущие фильтры
    из FSM, переключает выбранный фильтр, сбрасывает страницу на первую, получает
    отфильтрованный список пользователей и обновляет отображение с новыми фильтрами.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.

    Returns:
        None: ничего не возвращает.
    """

    filter_type = cq.data.split(":")[3]  # "active" или "blocked"

    # Получаем текущие фильтры
    data = await state.get_data()
    filters = data.get("filters", {"active": False, "blocked": False})

    # Переключаем фильтр
    filters[filter_type] = not filters.get(filter_type, False)
    await state.update_data(filters=filters)

    async with session_factory() as session:
        users, total_users = await get_users_page(
            session,
            page=1,  # Сбрасываем на первую страницу при изменении фильтра
            per_page=USERS_PER_PAGE,
            filters=filters,
        )

        text = await build_users_page_text(session, users)
        kb = kb_admin_users(page=1, total_users=total_users, filters=filters)

        await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("admin:users:"))
async def cb_admin_users_page(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback-запросы для переключения страниц списка пользователей.

    Извлекает номер страницы из callback data, проверяет что это не запрос фильтра,
    получает текущие фильтры из FSM, получает страницу пользователей с учётом фильтров,
    форматирует текст и обновляет отображение списка с навигацией по страницам.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.

    Returns:
        None: ничего не возвращает.
    """

    # Пропускаем обработку, если это фильтр (должен обработаться выше)
    if cq.data.startswith("admin:users:filter:"):
        return

    try:
        page = int(cq.data.split(":")[2])
    except ValueError:
        # Если не удалось преобразовать в число, игнорируем
        await cq.answer()
        return

    async with session_factory() as session:
        data = await state.get_data()
        filters = data.get("filters", {"active": False, "blocked": False})

        users, total_users = await get_users_page(
            session,
            page=page,
            per_page=USERS_PER_PAGE,
            filters=filters,  # ← ВАЖНО!
        )

        text = await build_users_page_text(session, users)
        kb = kb_admin_users(page, total_users, filters)

        await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()
