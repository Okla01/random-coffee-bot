"""
Обработчик кнопки выхода из административной панели.

Обрабатывает callback-запрос для выхода из административной панели. Сбрасывает флаг
активности панели в FSM, что позволяет обработчикам регистрации снова перехватывать
текстовые сообщения, и обновляет сообщение с уведомлением о выходе, убирая inline-клавиатуру.
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

    Сбрасывает флаг активности панели в FSM, обновляет сообщение с уведомлением о выходе
    и убирает inline-клавиатуру. Это позволяет обработчикам регистрации снова перехватывать
    текстовые сообщения.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    # Сброс состояния панели администратора
    await state.update_data(**{FSMDataKeys.ADMIN_PANEL_ACTIVE: False})

    await cq.message.edit_text(
        "Вы вышли из административной панели.", reply_markup=None
    )
