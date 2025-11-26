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

from app.keyboards.kb_admin import kb_admin_settings
from app.services.admin.settings import get_current_settings
from app.services.core import Settings

router = Router()


class AdminSettingsStates(StatesGroup):
    """Состояния FSM для редактирования настроек администратора."""

    waiting_min_jaccard = State()
    waiting_cooldown_weeks = State()
    waiting_match_day = State()
    waiting_match_utc_hour = State()


# Словарь дней недели
DAYS_OF_WEEK = {
    "mon": "Понедельник",
    "tue": "Вторник",
    "wed": "Среда",
    "thu": "Четверг",
    "fri": "Пятница",
    "sat": "Суббота",
    "sun": "Воскресенье",
}


@router.callback_query(F.data == "admin:settings")
async def cb_admin_settings(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Обрабатывает callback для открытия меню настроек администратора."""

    # Получение словаря текущих настроек
    current_settings = await get_current_settings(session_factory)

    # Формирование текста меню настроек
    text = "Настройки для организации встреч.\n\n"
    text += f"🔹 Минимальный Jaccard: {current_settings['min_jaccard']}\n"
    text += f"🔹 Периодичность встреч: {current_settings['cooldown_weeks']}\n"
    text += f"🔹 День недели для встреч: {DAYS_OF_WEEK[current_settings['match_day']]}\n"
    text += f"🔹 Час совпадения: {current_settings['match_utc_hour']}\n"

    # Отображение меню настроек
    sent = await cq.message.answer(
        text,
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(last_kb_mid=sent.message_id)


@router.callback_query(F.data == "admin:update_min_jaccard")
async def cb_update_min_jaccard(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Запрашивает новое значение минимального Jaccard коэффициента."""
    await cq.answer("Заглушка: изменение min_jaccard")


@router.callback_query(F.data == "admin:update_cooldown_weeks")
async def cb_update_cooldown_weeks(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Запрашивает новое значение периодичности встреч в неделях."""
    await cq.answer("Заглушка: изменение cooldown_weeks")


@router.callback_query(F.data == "admin:update_match_day")
async def cb_update_match_day(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Запрашивает новый день недели для встреч."""
    await cq.answer("Заглушка: изменение match_day")


@router.callback_query(F.data == "admin:update_match_utc_hour")
async def cb_update_match_utc_hour(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Запрашивает новый час совпадения (UTC)."""
    await cq.answer("Заглушка: изменение match_utc_hour")


@router.message(
    StateFilter(
        AdminSettingsStates.waiting_min_jaccard,
        AdminSettingsStates.waiting_cooldown_weeks,
        AdminSettingsStates.waiting_match_day,
        AdminSettingsStates.waiting_match_utc_hour,
    )
)
async def on_setting_value_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Обрабатывает ввод нового значения настройки."""
    await message.answer("Заглушка: обработка ввода настройки")


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
