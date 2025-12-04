"""
Код обработчика кнопки выхода из административной панели.

Очищает состояние FSM, удаляет inline-клавиатуру из последнего сообщения
и отправляет сообщение о выходе из административной панели.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.handlers.fsm import FSMDataKeys


router = Router()


@router.callback_query(F.data == "admin:exit")
async def cb_admin_exit(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Обрабатывает нажатие кнопки выхода из административной панели.

    Сбрасывает все стейты в оперативной памяти, удаляет inline-клавиатуру
    из последнего сообщения и отправляет сообщение о выходе из административной панели.
    """
    # Сброс состояния панели администратора
    await state.update_data(**{FSMDataKeys.ADMIN_PANEL_ACTIVE: False})

    await cq.message.edit_text("Вы вышли из административной панели.", reply_markup=None)
