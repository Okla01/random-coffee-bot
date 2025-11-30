"""
Inline-клавиатуры для административной панели управления пользователями.

Содержит генератор клавиатур для принятия решений по блокировке/разблокировке пользователей.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.const import DAYS_OF_WEEK


def kb_admin_menu() -> InlineKeyboardMarkup:
    """
    Генерирует основную клавиатуру панели администратора.

    Содержит кнопки для управления пользователями и просмотра отчетов.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users"),
            ],
            [
                InlineKeyboardButton(text="🚩 Жалобы", callback_data="admin:complaints"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="admin:statistics"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin:settings"),
            ],
            [
                InlineKeyboardButton(text="📤 Экспорт Excel", callback_data="admin:export"),
            ],
            [
                InlineKeyboardButton(text="⛔ Выход", callback_data="admin:exit"),
            ],
        ]
    )


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


def kb_admin_settings() -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для настроек администратора.

    Используется для изменения специфических настроек работы алгоритма организации встреч.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Изменить минимальный Jaccard", callback_data="admin:update_min_jaccard"),
            ],
            [
                InlineKeyboardButton(text="🔄 Изменить периодичность встреч (в неделях)", callback_data="admin:update_cooldown_weeks"),
            ],
            [
                InlineKeyboardButton(text="🗓️ Изменить день недели для встреч", callback_data="admin:update_match_day"),
            ],
            [
                InlineKeyboardButton(text="🕓 Изменить час совпадения", callback_data="admin:update_match_msk_hour"),
            ],
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="admin:save_admin_settings"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin:cancel_admin_settings"),
            ],
        ]
    )


def kb_admin_settings_change_day_of_week(current_day_of_week: str) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для выбора дня недели для встреч.

    Args:
        current_day_of_week (str): код текущего дня недели (mon, tue, wed...).
    """
    keyboard = []
    row = []
    
    for day_code, label in DAYS_OF_WEEK.items():
        if len(row) == 3:
            keyboard.append(row)
            row = []

        mark = "✅" if day_code == current_day_of_week else "❌"
        row.append(
            InlineKeyboardButton(
                text=f"{mark} {label}",
                callback_data=f"admin:change_day_of_week:{day_code}"
            )
        )

    keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def kb_admin_back_to_menu() -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для возврата в главное меню администратора.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back_to_menu"),
            ]
        ]
    )