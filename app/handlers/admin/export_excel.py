"""
Обработчик для кнопки экспорта Excel панели администратора.

Экспортирует всех пользователей в Excel файл.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.handlers.fsm import FSMDataKeys
from app.keyboards.kb_admin import kb_admin_back_to_menu, kb_admin_menu
from app.keyboards.utils import clear_last_kb
from app.services.admin.export_excel import export_users_to_excel


router = Router()


# ----------------- Экспорт Excel -----------------

@router.callback_query(F.data == "admin:export")
async def cb_admin_export_excel(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback для экспорта Excel панели администратора.
    """
    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    # Получение Excel документа с пользователями
    document = await export_users_to_excel(session_factory)

    sent = await cq.message.answer_document(
        document,
        caption="Экспорт пользователей в Excel-таблицу",
        reply_markup=kb_admin_back_to_menu(),
    )

    await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})


# ----------------- Возврат в меню администратора -----------------

@router.callback_query(F.data == "admin:back_to_menu")
async def cb_admin_back_to_menu(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Обрабатывает callback для возврата в главное меню администратора.
    """
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)
    sent = await cq.message.answer(
        "Админ-панель открыта.\nДействия по заявкам будут приходить в админ-чат при блокировках.",
        reply_markup=kb_admin_menu(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
