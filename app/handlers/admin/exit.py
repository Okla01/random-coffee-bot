"""
Код обработчика кнопки выхода из административной панели.

Очищает состояние FSM, удаляет inline-клавиатуру из последнего сообщения
и отправляет сообщение о выходе из административной панели.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.db import get_or_create_user, get_user_by_tg_id
from app.handlers.fsm import FSMDataKeys
from app.keyboards.utils import clear_last_kb
from app.services.core.config import Settings
from app.services.onboarding import handle_start_result, process_start


router = Router()


@router.callback_query(F.data == "admin:exit")
async def cb_admin_exit(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings
) -> None:
    """
    Обрабатывает нажатие кнопки выхода из административной панели.

    Сбрасывает все стейты в оперативной памяти, удаляет inline-клавиатуру
    из последнего сообщения и отправляет сообщение о выходе из административной панели.
    """
    # Сброс состояния панели администратора
    await state.update_data(**{FSMDataKeys.ADMIN_PANEL_ACTIVE: False})

    await cq.message.edit_reply_markup(reply_markup=None)
    
    # Открываем асинхронную сессию БД в контекстном менеджере
    async with session_factory() as session:
        # Получаем пользователя или создаём нового при первом заходе
        user = await get_or_create_user(
            session, cq.message.from_user.id, cq.message.from_user.username
        )

        # Удаление последней клавиатуры
        await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

        # Вызываем бизнес-логику онбординга, которая решит, что делать дальше
        result = await process_start(session, user, settings)

        await cq.message.answer("Вы вышли из административной панели.")

        # Возвращение в исходное состояние (которое было до входа в админ-панель)
        await handle_start_result(
            cq.message,
            state,
            user,
            result
        )
    
    