"""
Обработчики блокировки и разблокировки пользователей.

Обрабатывает callback-запросы для блокировки/разблокировки пользователей
с уведомлениями и логированием действий.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.core import Settings
from app.database import User
from app.services.admin import is_admin, block_user, unblock_user

router = Router()


@router.callback_query()
async def admin_callbacks(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает callback-запросы для блокировки/разблокировки пользователей.

    Интерпретирует callback data формата 'admin:block:ID' или 'admin:unblock:ID'.
    Изменяет статус пользователя, уведомляет его о решении, логирует действие
    и обновляет исходное сообщение с указанием администратора.
    Отказывает в доступе если caller не администратор.
    """
    data = cq.data or ""
    if not (data.startswith("admin:block:") or data.startswith("admin:unblock:")):
        return

    async with session_factory() as session:
        # Проверка прав администратора
        if not await is_admin(session, settings, cq.from_user.id):
            await cq.answer("Нет прав")
            return

        _, action, user_id_str = data.split(":")
        target_id = int(user_id_str)

        user = (
            await session.execute(select(User).where(User.id == target_id))
        ).scalar_one_or_none()

        if not user:
            await cq.answer("Пользователь не найден")
            return

        reviewed_by = cq.from_user.username or str(cq.from_user.id)

        if action == "block":
            # Блокировка пользователя
            await block_user(session, cq.from_user.id, user)

            # Обновляем исходное сообщение: дописываем решение и убираем inline-клавиатуру
            await cq.message.edit_text(
                cq.message.text
                + f"\n\nРешение: Пользователь {'@' + user.username} заблокирован."
                  f"\n👨‍💻Рассмотрел: {'@' + reviewed_by}",
                reply_markup=None,
            )

            # Пытаемся уведомить пользователя
            if user.telegram_id:
                try:
                    await cq.message.bot.send_message(
                        user.telegram_id,
                        (
                            "Решение по временной блокировке: Вам закрыт доступ. "
                            "Если считаете это ошибкой — обратитесь к администратору."
                        ),
                    )
                except Exception:
                    # Здесь можно залогировать, если используешь логгер
                    pass

        else:
            # Разблокировка пользователя
            await unblock_user(session, cq.from_user.id, user)

            # Обновляем исходное сообщение: дописываем решение и убираем inline-клавиатуру
            await cq.message.edit_text(
                cq.message.text
                + f"\n\nРешение: Пользователь {'@' + user.username} разблокирован "
                  f"и возвращён к вводу корпоративного e-mail."
                  f"\n👨‍💻Рассмотрел: {'@' + reviewed_by}",
                reply_markup=None,
            )

            # Уведомляем пользователя
            if user.telegram_id:
                try:
                    await cq.message.bot.send_message(
                        user.telegram_id,
                        (
                            "Решение по временной блокировке: Вас разблокировали. "
                            "Пожалуйста, пройдите регистрацию заново.\n"
                            "Введите корпоративный e-mail:"
                        ),
                    )
                except Exception:
                    # Здесь тоже можно залогировать
                    pass

        # Закрываем "часы" у колбэка
        await cq.answer()

