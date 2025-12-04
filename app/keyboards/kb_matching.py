"""
Inline-клавиатуры для сценариев матчинга Random Coffee.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb_match_invitation(match_id: int) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру с кнопками «Готов познакомиться» / «Пропустить».

    Используется при отправке приглашения на встречу после создания матча.

    Args:
        match_id (int): ID матча для формирования callback_data.

    Returns:
        InlineKeyboardMarkup: inline-клавиатура с двумя кнопками.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Готов познакомиться ☕️",
                    callback_data=f"match_ready:{match_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Пропустить на этой неделе",
                    callback_data=f"match_skip:{match_id}",
                )
            ],
        ]
    )


def kb_match_confirm_prompt(match_id: int) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру подтверждения встречи или повторного выбора времени.

    Используется на этапе waiting_confirm, когда найдено пересечение слотов.

    Args:
        match_id (int): ID матча для формирования callback_data.

    Returns:
        InlineKeyboardMarkup: inline-клавиатура с кнопками «Подтвердить» и «Назначить заново».
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=f"match_confirm:{match_id}",
                ),
                InlineKeyboardButton(
                    text="Назначить заново",
                    callback_data=f"match_reschedule:{match_id}",
                ),
            ],
        ]
    )