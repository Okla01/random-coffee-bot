"""
Inline-клавиатуры для сценария заполнения профиля пользователя.

Содержит функции-генераторы inline-клавиатур для различных этапов заполнения анкеты:
сохранение, редактирование отдельных полей, участие в подборе.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.const import (
    INTERESTS_PAGE_SIZE,
    MAX_INTERESTS_COUNT,
    MIN_INTERESTS_COUNT,
    UNIVERSAL_INTERESTS,
)


def kb_profile_review() -> InlineKeyboardMarkup:
    """
    Кратко: клавиатура предпросмотра анкеты с действиями для редактирования.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сохранить ✅", callback_data="prof:save")],
            [
                InlineKeyboardButton(
                    text="Изменить описание", callback_data="prof:edit:bio"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Изменить интересы", callback_data="prof:edit:interests"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Изменить фото", callback_data="prof:edit:photo"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Удалить профиль 🗑", callback_data="prof:delete:confirm"
                )
            ],
        ]
    )


def kb_profile_photo_clear_save() -> InlineKeyboardMarkup:
    """
    Кратко: клавиатура для управления фотографиями профиля (очистка, сохранить).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Очистить🗑️", callback_data="prof:photo:clear"
                ),
                InlineKeyboardButton(
                    text="Сохранить ✅", callback_data="prof:photo:save"
                ),
            ],
        ]
    )


def kb_profile_photo() -> InlineKeyboardMarkup:
    """
    Кратко: клавиатура для управления фотографиями профиля (без загруженных фото).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Взять фото из профиля 👤", callback_data="prof:photo:from_tg"
                )
            ],
        ]
    )


def kb_profile_photo_with_photos() -> InlineKeyboardMarkup:
    """
    Кратко: клавиатура для управления фотографиями профиля (с загруженными фото).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Взять фото из профиля 👤", callback_data="prof:photo:from_tg"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Добавить ➕", callback_data="prof:photo:add"
                ),
                InlineKeyboardButton(
                    text="Очистить 🗑️", callback_data="prof:photo:clear"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Сохранить ✅", callback_data="prof:photo:save"
                )
            ],
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
                InlineKeyboardButton(
                    text="Отмена ❌", callback_data="prof:delete:cancel"
                ),
            ],
        ]
    )


def kb_profile_interests(
    selected: list[str],
    page: int,
    *,
    per_page: int = INTERESTS_PAGE_SIZE,
    min_required: int = MIN_INTERESTS_COUNT,
    max_allowed: int = MAX_INTERESTS_COUNT,
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора интересов с пагинацией.
    """
    total = len(UNIVERSAL_INTERESTS)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = min(start + per_page, total)
    chunk = UNIVERSAL_INTERESTS[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for offset, interest in enumerate(chunk):
        global_index = start + offset
        prefix = "✅ " if interest in selected else "• "
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{interest}",
                    callback_data=f"prof:int:sel:{global_index}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Назад", callback_data=f"prof:int:page:{page - 1}"
            )
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Вперёд ➡️", callback_data=f"prof:int:page:{page + 1}"
            )
        )
    if nav_row:
        rows.append(nav_row)

    if selected:
        actions: list[InlineKeyboardButton] = [
            InlineKeyboardButton(text="Очистить 🗑", callback_data="prof:int:clear")
        ]
        if min_required <= len(selected) <= max_allowed:
            actions.append(
                InlineKeyboardButton(text="Сохранить ✅", callback_data="prof:int:save")
            )
        rows.append(actions)

    return InlineKeyboardMarkup(inline_keyboard=rows)
