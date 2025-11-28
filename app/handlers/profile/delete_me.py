"""
Обработчики для удаления профиля пользователя.

Реализует сценарий подтверждения и удаления профиля пользователя:
- показ подтверждения удаления
- удаление пользователя из БД
- отмена удаления с возвратом к просмотру анкеты
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.core import Settings
from app.services.profile.delete_me import delete_user_by_telegram_id
from app.services.profile.preview import send_profile_preview

from app.keyboards.utils import clear_last_kb
from app.keyboards.kb_profile import (
    kb_profile_delete_confirm,
    kb_profile_review,
)

from app.database.db import get_or_create_user
from app.handlers.fsm import FSMDataKeys


router = Router()


@router.callback_query(F.data == "prof:delete:confirm")
async def cb_delete_confirm(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Удалить профиль 🗑» — показывает подтверждение.

    Удаляет предыдущую клавиатуру и отправляет сообщение с подтверждением удаления
    и кнопками «Удалить 🗑» и «Отмена ❌».

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    # Удаляем предыдущую клавиатуру
    await cq.message.edit_reply_markup(reply_markup=None)
    
    # Отправляем сообщение с подтверждением
    sent = await cq.message.answer(
        "Точно ли вы хотите удалить свою анкету?",
        reply_markup=kb_profile_delete_confirm(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
    await cq.answer()


@router.callback_query(F.data == "prof:delete:yes")
async def cb_delete_yes(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Удалить 🗑» — удаляет пользователя из БД.

    Удаляет последнюю клавиатуру, находит пользователя по telegram_id,
    полностью удаляет его из БД, сбрасывает все стейты в оперативной памяти
    и отправляет сообщение "Анкета удалена.".

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    # Удаляем последнюю клавиатуру
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)
    
    async with session_factory() as session:
        # Удаляем пользователя напрямую по telegram_id
        await delete_user_by_telegram_id(session, cq.from_user.id)
        
        # Сбрасываем все стейты в оперативной памяти
        await state.clear()
        
        # Отправляем сообщение об удалении
        await cq.message.answer("Анкета удалена.")
    
    await cq.answer()


@router.callback_query(F.data == "prof:delete:cancel")
async def cb_delete_cancel(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Отмена ❌» — отменяет удаление.

    Удаляет последнюю клавиатуру и возвращает пользователя к просмотру анкеты.

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    # Удаляем последнюю клавиатуру
    await cq.message.edit_text("Удаление отменено.", reply_markup=None)
    
    async with session_factory() as session:
        user = await get_or_create_user(session, cq.from_user.id, cq.from_user.username)
        
        # Возвращаем пользователя к просмотру анкеты с фотографиями
        await send_profile_preview(
            cq.message.bot, cq.message.chat.id, user, state, kb_profile_review()
        )
    
    await cq.answer()
