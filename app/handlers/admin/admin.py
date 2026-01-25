"""
Обработчик команды /admin для открытия административной панели.

Проверяет права администратора (по ADMIN_IDS или роли), создаёт пользователя при необходимости,
логирует открытие панели в AdminLog, устанавливает флаг активности панели в FSM для предотвращения
перехвата текстовых сообщений обработчиками регистрации и отображает главное меню администратора.
Отказывает в доступе если пользователь заблокирован или не имеет прав администратора.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.keyboards.kb_admin import kb_admin_menu
from app.services.core import Settings
from app.keyboards.utils import clear_last_kb
from app.services.admin import (
    process_admin_command,
    AdminAccessResultType,
)
from app.handlers.fsm import FSMDataKeys

router = Router()


@router.message(Command("admin"))
async def cmd_admin(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает команду /admin — открывает административную панель.

    Проверяет права администратора (по ADMIN_IDS или роли), создаёт пользователя
    если его нет в БД, логирует открытие панели и отправляет приветственное сообщение.
    Отказывает в доступе если пользователь заблокирован.

    Args:
        message (Message): объект сообщения /admin.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        result_type, user = await process_admin_command(
            session,
            settings,
            message.from_user.id,
            message.from_user.username,
        )

        if result_type == AdminAccessResultType.NO_RIGHTS:
            await message.answer("⛔️ Нет прав.")
            return

        await session.commit()

        # Удаляем предыдущую клавиатуру
        await clear_last_kb(state, message.chat.id, message.bot)

        # Сохраняем флаг, что админ-панель активна, чтобы обработчик регистрации не перехватывал текст
        await state.update_data(**{FSMDataKeys.ADMIN_PANEL_ACTIVE: True})

        # Отображение админ-панели после всех проверок
        sent = await message.answer(
            "Админ-панель открыта.\nДействия по заявкам будут приходить в админ-чат.",
            reply_markup=kb_admin_menu(),
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
