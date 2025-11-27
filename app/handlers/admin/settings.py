"""
Обработчик кнопок с настройками панели администратора.

Обрабатывает callback-запросы для просмотра и изменения настроек системы:
- минимальный Jaccard коэффициент
- периодичность встреч (в неделях)
- день недели для встреч
- час совпадения (UTC)
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.keyboards.kb_admin import kb_admin_settings, kb_admin_settings_change_day_of_week
from app.keyboards.utils import clear_last_kb
from app.services.admin.settings import format_settings_text, get_current_settings, try_to_input_cooldown_weeks, try_to_input_min_jaccard, update_draft_setting
from app.services.core import Settings

router = Router()


class AdminSettingsStates(StatesGroup):
    """Состояния FSM для редактирования настроек администратора."""

    waiting_min_jaccard = State()
    waiting_cooldown_weeks = State()
    waiting_match_day = State()
    waiting_match_utc_hour = State()


# ----------------------------- Обработчик главной кнопки "Настройки" -----------------------------

@router.callback_query(F.data == "admin:settings")
async def cb_admin_settings(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Обрабатывает callback для открытия меню настроек администратора."""

    # Получение словаря текущих настроек
    current_settings = await get_current_settings(session_factory)

    # Сохранение текущих настроек в состояние
    # как черновик для последующего сохранения или отмены изменений
    await state.update_data(draft_settings=current_settings)

    # Текст содержит настройки, которые ещё не сохранены в базу данных
    text = format_settings_text(current_settings)

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)
    # Отображение меню настроек
    sent = await cq.message.answer(
        text,
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(last_kb_mid=sent.message_id)


# ----------------------------- Обработчики кнопок меню настроек -----------------------------

@router.callback_query(F.data == "admin:update_min_jaccard")
async def cb_update_min_jaccard(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Запрашивает новое значение минимального Jaccard коэффициента."""

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    await cq.message.answer("Введите новое значение минимального Jaccard коэффициента (0,1 - 1,0):")
    
    # Переход с состояние ожидания значения
    await state.set_state(AdminSettingsStates.waiting_min_jaccard)


@router.callback_query(F.data == "admin:update_cooldown_weeks")
async def cb_update_cooldown_weeks(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Запрашивает новое значение периодичности встреч в неделях."""

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    await cq.message.answer("Раз во сколько недель проводить встречи? (1 - 4)")

    # Переход с состояние ожидания значения
    await state.set_state(AdminSettingsStates.waiting_cooldown_weeks)


@router.callback_query(F.data == "admin:update_match_day")
async def cb_update_match_day(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Запрашивает новый день недели для встреч."""
    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    data = await state.get_data()
    draft = (data.get("draft_settings") or {}).copy()

    sent = await cq.message.answer(
        "Выберите новый день недели для встреч:",
        reply_markup=kb_admin_settings_change_day_of_week(draft["match_day"]),
    )
    await state.update_data(last_kb_mid=sent.message_id)


@router.callback_query(F.data == "admin:update_match_utc_hour")
async def cb_update_match_utc_hour(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Запрашивает новый час совпадения (UTC)."""
    await cq.answer("Заглушка: изменение match_utc_hour")


@router.callback_query(F.data == "admin:save_admin_settings")
async def cb_save_admin_settings(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Сохраняет все изменения настроек в базу данных."""
    await cq.answer("Заглушка: сохранение настроек")


@router.callback_query(F.data == "admin:cancel_admin_settings")
async def cb_cancel_admin_settings(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Отменяет все несохранённые изменения и возвращает в главное меню админа."""
    await cq.answer("Заглушка: отмена изменений")


# ----------------------------- Обработчик выбора дня недели -----------------------------

@router.callback_query(F.data.startswith("admin:change_day_of_week:"))
async def cb_change_day_of_week(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор нового дня недели для встреч."""
    # Получение кода дня из callback_data
    # "admin:change_day_of_week:mon" -> "mon"
    day_code = cq.data.split(":")[-1]

    # Обновление дня недели в черновых настройках
    draft = await update_draft_setting(state, "match_day", day_code)

    # Выход из состояния ожидания значения
    await state.set_state(None)

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    # Возвращение в меню настроек
    sent = await cq.message.answer(
        format_settings_text(draft),
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(last_kb_mid=sent.message_id)


# ----------------------------- Обработчики состояний ожидания значений настроек -----------------------------

@router.message(StateFilter(AdminSettingsStates.waiting_min_jaccard))
async def on_min_jaccard_input(
    msg: Message,
    state: FSMContext
) -> None:
    """
    Обрабатывает ввод нового значения минимального Jaccard коэффициента.

    Примечание: Некорректный ввод обрабатывается отдельно.
    """
    min_jaccard: float | None = try_to_input_min_jaccard(msg.text)
    if min_jaccard is  None:
        await msg.answer("Некорректный ввод. Пожалуйста, введите число в диапазоне\n0,1 - 1,0.")
        return

    draft = await update_draft_setting(state, "min_jaccard", min_jaccard)
    await state.update_data(draft_settings=draft)

    # Выход из состояния ожидания значения
    await state.set_state(None)

    # Возвращение в меню настроек
    sent = await msg.answer(
        format_settings_text(draft),
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(last_kb_mid=sent.message_id)


@router.message(StateFilter(AdminSettingsStates.waiting_cooldown_weeks))
async def on_cooldown_weeks_input(
    msg: Message,
    state: FSMContext
) -> None:
    """
    Обрабатывает ввод нового значения периодичности встреч в неделях.
    """
    cooldown_weeks: int | None = try_to_input_cooldown_weeks(msg.text)
    if cooldown_weeks is None:
        await msg.answer("Некорректный ввод. Пожалуйста, введите число в диапазоне 1 - 4.")
        return
    
    draft = await update_draft_setting(state, "cooldown_weeks", cooldown_weeks)
    await state.update_data(draft_settings=draft)

    # Выход из состояния ожидания значения
    await state.set_state(None)

    # Возвращение в меню настроек
    sent = await msg.answer(
        format_settings_text(draft),
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(last_kb_mid=sent.message_id)
