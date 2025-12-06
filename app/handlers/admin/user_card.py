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
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import User, AdminLog
from app.database.db import is_user_blocked, get_user_by_id
from app.handlers.fsm import AdminMessageStates, FSMDataKeys
from app.keyboards.kb_admin import kb_admin_user_actions, kb_admin_message_cancel
from app.services.admin import (
    is_admin,
    grant_admin_role,
    revoke_admin_role,
)
from app.services.core import Settings
from app.services.const import USER_STATUS_BLOCKED, USER_STATUS_NEW

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

    user = await get_user_by_id(session, user_id)

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
            # Блокируем пользователя
            user.status = USER_STATUS_BLOCKED
            
            # Логируем действие
            session.add(
                AdminLog(
                    admin_id=cq.from_user.id,
                    action="block",
                    payload={"user_id": user.id},
                )
            )
            await session.commit()
            
            await cq.answer(f"✅ {username_display} заблокирован")

        else:
            # Разблокируем пользователя
            user.status = USER_STATUS_NEW
            
            # Логируем действие
            session.add(
                AdminLog(
                    admin_id=cq.from_user.id,
                    action="unblock",
                    payload={"user_id": user.id},
                )
            )
            await session.commit()
            
            await cq.answer(f"✅ {username_display} разблокирован")

            # Уведомляем пользователя
            try:
                await cq.bot.send_message(
                    user.telegram_id,
                    "Вас разблокировали.",
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


# ----------------------------- Отправка сообщения пользователю ----------------------------- #


@router.callback_query(F.data.startswith("admin:message:"))
async def cb_message_start(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Начинает процесс отправки сообщения пользователю — запрашивает текст.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    data = cq.data or ""
    _, _, user_id_str = data.split(":")
    target_id = int(user_id_str)

    async with session_factory() as session:
        user = await _get_user_and_check_admin(cq, session, settings, target_id)
        if not user:
            return

        username_display = (
            f"@{user.username}" if user.username else f"ID:{user.telegram_id}"
        )

        # Сохраняем контекст в FSM
        await state.update_data(
            **{
                FSMDataKeys.ADMIN_MESSAGE_USER_ID: user.id,
            }
        )
        await state.set_state(AdminMessageStates.waiting_message_text)

        # Просим ввести текст сообщения
        cancel_message = await cq.message.answer(
            f"📝 Введите текст сообщения для пользователя {username_display}:",
            reply_markup=kb_admin_message_cancel(),
        )

        # Сохраняем ID сообщения с кнопкой отмены для последующего удаления
        await state.update_data(
            **{
                FSMDataKeys.ADMIN_MESSAGE_CANCEL_MESSAGE_ID: cancel_message.message_id,
            }
        )
        await cq.answer()


@router.callback_query(F.data == "admin:cancel_message")
async def cb_cancel_message(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Отменяет ввод текста сообщения.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    await state.clear()
    await cq.message.delete()
    await cq.answer("❌ Отменено")


@router.message(AdminMessageStates.waiting_message_text)
async def handle_message_text(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает ввод текста сообщения и отправляет его пользователю.

    Args:
        message (Message): объект сообщения с текстом.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    message_text = message.text

    if not message_text or len(message_text.strip()) == 0:
        await message.answer(
            "❌ Текст сообщения не может быть пустым. Попробуйте ещё раз:"
        )
        return

    message_text = message_text.strip()

    # Получаем данные из FSM
    data = await state.get_data()
    target_user_id = data.get(FSMDataKeys.ADMIN_MESSAGE_USER_ID)
    cancel_message_id = data.get(FSMDataKeys.ADMIN_MESSAGE_CANCEL_MESSAGE_ID)

    # Удаляем сообщение с кнопкой отмены
    if cancel_message_id:
        try:
            await message.bot.delete_message(
                chat_id=message.chat.id,
                message_id=cancel_message_id,
            )
        except Exception:
            pass

    if not target_user_id:
        await message.answer("❌ Произошла ошибка. Попробуйте начать заново.")
        await state.clear()
        return

    async with session_factory() as session:
        # Проверяем права администратора
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            await state.clear()
            return

        # Получаем пользователя
        user = await get_user_by_id(session, target_user_id)

        if not user:
            await message.answer("❌ Пользователь не найден.")
            await state.clear()
            return

        username_display = (
            f"@{user.username}" if user.username else f"ID:{user.telegram_id}"
        )

        # Отправляем сообщение пользователю
        try:
            await message.bot.send_message(
                user.telegram_id,
                f"📩 <b>Сообщение от администратора:</b>\n\n{message_text}",
                parse_mode="HTML",
            )

            # Логируем действие
            session.add(
                AdminLog(
                    admin_id=message.from_user.id,
                    action="send_message",
                    payload={
                        "user_id": user.id,
                        "message_text": message_text[:500],  # Ограничиваем для лога
                    },
                )
            )
            await session.commit()

            await message.answer(f"✅ Сообщение отправлено пользователю {username_display}.")
        except Exception:
            await message.answer(
                f"❌ Не удалось отправить сообщение пользователю {username_display}.\n\n"
                "Возможные причины:\n"
                "• Пользователь заблокировал бота\n"
                "• Пользователь удалил аккаунт\n"
                "• Техническая ошибка"
            )

        await state.clear()
