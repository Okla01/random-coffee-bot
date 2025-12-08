"""
Обработчики статистики в административной панели.

Обрабатывает callback-запросы для просмотра статистики за различные периоды (7 дней,
30 дней, 6 месяцев, всё время), экспорта статистики в Excel файл и генерации графиков
статистики за 6 месяцев. Форматирует и отображает статистику по пользователям, мэтчам,
жалобам и другим метрикам системы.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, InputMediaPhoto, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.handlers.fsm import FSMDataKeys
from app.keyboards.kb_admin import kb_admin_back_to_menu, kb_admin_statistics
from app.keyboards.utils import clear_last_kb
from app.services.admin.statistics import get_all_statistics, format_statistics_text
from app.services.admin.statistics_export import export_statistics_to_excel
from app.services.admin.statistics_graphs import generate_statistics_graphs

router = Router()

# ------------------------- Обработчик кнопки "Статистика" -------------------------


@router.callback_query(F.data == "admin:statistics")
async def cb_admin_statistics(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback для просмотра статистики (по умолчанию 7 дней).

    Удаляет предыдущую клавиатуру, получает статистику за 7 дней из базы данных,
    форматирует текст и отображает меню статистики с возможностью выбора периода.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.

    Returns:
        None: ничего не возвращает.
    """

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    async with session_factory() as session:
        stats = await get_all_statistics(session, period="7_days")
        text = format_statistics_text(stats)

    # Редактируем текущее сообщение
    await cq.message.edit_text(text, reply_markup=kb_admin_statistics())

    # Сохранение ID последней отправленной клавиатуры
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


# ------------------------- Обработчики кнопок периодов -------------------------


@router.callback_query(
    F.data.in_(
        {
            "admin:statistics:7_days",
            "admin:statistics:30_days",
            "admin:statistics:6_months",
            "admin:statistics:all_time",
        }
    )
)
async def cb_statistics_period(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback для выбора периода статистики.

    Извлекает период из callback data (7_days, 30_days, 6_months, all_time),
    получает статистику за выбранный период, форматирует текст и обновляет
    отображение меню статистики.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.

    Returns:
        None: ничего не возвращает.
    """
    await cq.answer()

    # Извлекаем период из callback data
    period = cq.data.split(":")[-1]  # 7_days, 30_days, 6_months, all_time

    async with session_factory() as session:
        stats = await get_all_statistics(session, period=period)
        text = format_statistics_text(stats)

    try:
        await cq.message.edit_text(text, reply_markup=kb_admin_statistics())
    except TelegramBadRequest:
        # Сообщение не изменилось (тот же период уже отображён)
        pass


# ------------------------- Обработчик экспорта в Excel -------------------------


@router.callback_query(F.data == "admin:statistics:export_excel")
async def cb_statistics_export_excel(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback для экспорта статистики в Excel.

    Удаляет предыдущую клавиатуру, генерирует Excel документ со статистикой
    через сервисную функцию и отправляет его администратору с кнопкой возврата в меню.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.

    Returns:
        None: ничего не возвращает.
    """

    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    document = await export_statistics_to_excel(session_factory)

    sent = await cq.message.answer_document(
        document, caption="📊 Статистика", reply_markup=kb_admin_back_to_menu()
    )

    await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})


# ------------------------- Обработчик графиков -------------------------


@router.callback_query(F.data == "admin:statistics:6_months_graphs")
async def cb_statistics_graphs(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback для генерации графиков статистики.

    Уведомляет администратора о начале генерации, удаляет предыдущую клавиатуру,
    генерирует графики статистики за 6 месяцев (мэтчи и средний Jaccard-коэффициент)
    через сервисную функцию, отправляет их в виде медиа-группы и добавляет кнопку
    возврата в меню.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.

    Returns:
        None: ничего не возвращает.
    """

    await cq.answer("Генерирую графики...")

    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    matches_graph, jaccard_graph = await generate_statistics_graphs(session_factory)

    # Формируем медиагруппу из двух графиков
    media_group = [
        InputMediaPhoto(
            media=BufferedInputFile(matches_graph.read(), filename="matches.png"),
            caption="📊 Мэтчи за 6 месяцев",
        ),
        InputMediaPhoto(
            media=BufferedInputFile(jaccard_graph.read(), filename="jaccard.png"),
            caption="📈 Средний Jaccard-коэффициент за 6 месяцев",
        ),
    ]

    await cq.message.answer_media_group(media_group)

    # Отправляем кнопку "Назад" отдельным сообщением
    sent = await cq.message.answer(
        "Графики статистики", reply_markup=kb_admin_back_to_menu()
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
