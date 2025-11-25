"""
Inline-клавиатуры для административной панели управления пользователями.

Содержит генератор клавиатур для принятия решений по блокировке/разблокировке пользователей.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb_admin_decision(user_id: int) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для принятия решения по блокировке пользователя.

    Содержит кнопки для блокировки и разблокировки пользователя, используется
    в уведомлениях администратору при обнаружении подозрительной деятельности.

    Args:
        user_id (int): ID пользователя в БД (для формирования callback data).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Заблокировать 🔒", callback_data=f"admin:block:{user_id}"),
                InlineKeyboardButton(text="Разблокировать 🔓", callback_data=f"admin:unblock:{user_id}"),
            ]
        ]
    )
