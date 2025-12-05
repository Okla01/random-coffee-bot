"""
Inline-клавиатуры для сценария авторизации.

Содержит функции-генераторы inline-клавиатур для процесса верификации email и OTP-кода,
а также кнопки перехода к заполнению профиля после успешной авторизации.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb_auth_code_wait() -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для ожидания ввода OTP-кода.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить код повторно 🔁", callback_data="otp:change_email"
                )
            ],
        ]
    )


def kb_auth_code_expired() -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для ожидания ввода OTP-кода, если код был отправлен ранее, но истёк.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Отправить 📩", callback_data="otp:resend"),
            ],
            [
                InlineKeyboardButton(
                    text="Изменить email ✏️", callback_data="otp:change_email"
                ),
            ],
        ]
    )
