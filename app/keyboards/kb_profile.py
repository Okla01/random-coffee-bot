"""
Inline-клавиатуры для сценария заполнения профиля пользователя.

Содержит функции-генераторы inline-клавиатур для различных этапов заполнения анкеты:
сохранение, редактирование отдельных полей, участие в подборе.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb_profile_review() -> InlineKeyboardMarkup:
    """
    Кратко: клавиатура предпросмотра анкеты с действиями для редактирования.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сохранить ✅", callback_data="prof:save")],
            [InlineKeyboardButton(text="Изменить описание", callback_data="prof:edit:bio"),],
            [InlineKeyboardButton(text="Изменить интересы", callback_data="prof:edit:interests")],
            [InlineKeyboardButton(text="Изменить фото", callback_data="prof:edit:photo")],
            [InlineKeyboardButton(text="Удалить профиль 🗑", callback_data="prof:delete:confirm")],
        ]
    )


def kb_profile_photo_clear_save() -> InlineKeyboardMarkup:
    """
    Кратко: клавиатура для управления фотографиями профиля (очистка, сохранить).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Очистить 🗑️", callback_data="prof:photo:clear"),
                InlineKeyboardButton(text="Сохранить ✅", callback_data="prof:photo:save"),
            ],
        ]
    )


def kb_profile_photo() -> InlineKeyboardMarkup:
    """
    Кратко: клавиатура для управления фотографиями профиля (без загруженных фото).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Взять фото из профиля 👤", callback_data="prof:photo:from_tg")],
        ]
    )


def kb_profile_photo_with_photos() -> InlineKeyboardMarkup:
    """
    Кратко: клавиатура для управления фотографиями профиля (с загруженными фото).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Взять фото из профиля 👤", callback_data="prof:photo:from_tg")],
            [
                InlineKeyboardButton(text="Добавить ➕", callback_data="prof:photo:add"),
                InlineKeyboardButton(text="Очистить 🗑️", callback_data="prof:photo:clear"),
            ],
            [InlineKeyboardButton(text="Сохранить ✅", callback_data="prof:photo:save")],
        ]
    )


def kb_profile_delete_confirm() -> InlineKeyboardMarkup:
    """
    Кратко: клавиатура подтверждения удаления профиля.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Удалить 🗑", callback_data="prof:delete:yes"),
                InlineKeyboardButton(text="Отмена ❌", callback_data="prof:delete:cancel"),
            ],
        ]
    )
