"""
Обработчики колбэков для карточки пользователя из inline-поиска.

Обрабатывает callback-запросы для управления пользователями из карточки, отображаемой
после выбора результата inline-поиска: блокировку/разблокировку пользователей и назначение/
лишение прав администратора. Проверяет права администратора, предотвращает блокировку
и лишение прав самого себя, уведомляет пользователей о действиях и обновляет клавиатуру
действий в соответствии с текущим состоянием пользователя.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import User
from app.database.db import is_user_blocked
from app.keyboards.kb_admin import kb_admin_user_actions
from app.services.admin import (
    is_admin,
    block_user,
    unblock_user,
    grant_admin_role,
    revoke_admin_role,
)
from app.services.core import Settings

router = Router()


async def _get_user_and_check_admin(
    cq: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    user_id: int,
) -> User | None:
    """
    Проверяет права администратора и получает пользователя из БД.

    Проверяет права администратора у пользователя, выполняющего действие, получает
    пользователя по ID из базы данных. Если проверка не пройдена или пользователь
    не найден, уведомляет администратора и возвращает None.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        user_id (int): ID пользователя в БД.

    Returns:
        User | None: объект пользователя или None, если проверка не пройдена.
    """
    if not await is_admin(session, settings, cq.from_user.id):
        await cq.answer("Нет прав")
        return None

    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()

    if not user:
        await cq.answer("Пользователь не найден")
        return None

    return user


async def _update_keyboard(
    cq: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    user: User,
) -> None:
    """
    Обновляет клавиатуру действий после изменения состояния пользователя.

    Определяет текущее состояние пользователя (заблокирован ли, является ли администратором),
    формирует новую клавиатуру действий с соответствующими кнопками и обновляет
    reply_markup сообщения с карточкой пользователя.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        user (User): объект пользователя.

    Returns:
        None: ничего не возвращает.
    """
    user_is_blocked = is_user_blocked(user)
    user_is_admin = await is_admin(session, settings, user.telegram_id)

    keyboard = kb_admin_user_actions(
        user_id=user.id,
        is_blocked=user_is_blocked,
        is_admin=user_is_admin,
    )

    try:
        await cq.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


# ----------------------------- Блокировка/разблокировка ----------------------------- #


@router.callback_query(
    F.data.startswith("admin:block:") | F.data.startswith("admin:unblock:")
)
async def cb_block_unblock(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает блокировку/разблокировку пользователей.

    Извлекает действие (block/unblock) и ID пользователя из callback data, проверяет
    права администратора, предотвращает блокировку самого себя, выполняет блокировку
    или разблокировку через сервисную функцию, уведомляет пользователя о действии
    и обновляет клавиатуру действий.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    data = cq.data or ""
    _, action, user_id_str = data.split(":")
    target_id = int(user_id_str)

    async with session_factory() as session:
        user = await _get_user_and_check_admin(cq, session, settings, target_id)
        if not user:
            return

        # Проверка: администратор не может заблокировать самого себя
        if action == "block" and user.telegram_id == cq.from_user.id:
            await cq.answer("❌ Нельзя заблокировать самого себя")
            return

        username_display = (
            f"@{user.username}" if user.username else f"ID:{user.telegram_id}"
        )

        if action == "block":
            await block_user(session, cq.from_user.id, user)
            await cq.answer(f"✅ {username_display} заблокирован")

            # Уведомляем пользователя
            try:
                await cq.bot.send_message(
                    user.telegram_id,
                    "Доступ временно заблокирован. Свяжитесь с администратором.",
                )
            except Exception:
                pass
        else:
            await unblock_user(session, cq.from_user.id, user)
            await cq.answer(f"✅ {username_display} разблокирован")

            # Уведомляем пользователя
            try:
                await cq.bot.send_message(
                    user.telegram_id,
                    "Вас разблокировали. Пожалуйста, пройдите регистрацию заново.",
                )
            except Exception:
                pass

        # Обновляем клавиатуру
        await _update_keyboard(cq, session, settings, user)


# ----------------------------- Назначение/лишение прав администратора ----------------------------- #


@router.callback_query(
    F.data.startswith("admin:make_admin:") | F.data.startswith("admin:remove_admin:")
)
async def cb_admin_role(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает назначение/лишение прав администратора.

    Извлекает действие (make_admin/remove_admin) и ID пользователя из callback data,
    проверяет права администратора, предотвращает лишение прав самого себя, выполняет
    назначение или лишение роли администратора через сервисную функцию и обновляет
    клавиатуру действий.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    data = cq.data or ""
    parts = data.split(":")
    action = parts[1]  # "make_admin" или "remove_admin"
    target_id = int(parts[2])

    async with session_factory() as session:
        user = await _get_user_and_check_admin(cq, session, settings, target_id)
        if not user:
            return

        # Проверка: администратор не может лишить себя прав администратора
        if action == "remove_admin" and user.telegram_id == cq.from_user.id:
            await cq.answer("❌ Нельзя лишить себя прав администратора")
            return

        username_display = (
            f"@{user.username}" if user.username else f"ID:{user.telegram_id}"
        )

        if action == "make_admin":
            await grant_admin_role(session, cq.from_user.id, user)
            await cq.answer(f"✅ {username_display} назначен администратором")
        else:
            await revoke_admin_role(session, cq.from_user.id, user)
            await cq.answer(f"✅ {username_display} лишён прав администратора")

        # Обновляем клавиатуру
        await _update_keyboard(cq, session, settings, user)
