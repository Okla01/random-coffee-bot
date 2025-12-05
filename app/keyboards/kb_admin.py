"""
Inline-клавиатуры для административной панели управления пользователями.

Содержит генератор клавиатур для принятия решений по блокировке/разблокировке пользователей.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.const import DAYS_OF_WEEK, USERS_PER_PAGE


def kb_admin_menu() -> InlineKeyboardMarkup:
    """
    Генерирует основную клавиатуру панели администратора.

    Содержит кнопки для управления пользователями и просмотра отчетов.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👥 Пользователи", callback_data="admin:users"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика", callback_data="admin:statistics"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Настройки", callback_data="admin:settings"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📤 Экспорт Excel", callback_data="admin:export"
                ),
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
                InlineKeyboardButton(
                    text="Заблокировать 🔒",
                    callback_data=f"admin:notify:block:{user_id}",
                ),
                InlineKeyboardButton(
                    text="Разблокировать 🔓",
                    callback_data=f"admin:notify:unblock:{user_id}",
                ),
            ]
        ]
    )


def kb_admin_name_approval(user_id: int) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для принятия решения по заявке на доступ к анкетированию.

    Содержит кнопки для одобрения и отклонения заявки пользователя на доступ к анкете.

    Args:
        user_id (int): ID пользователя в БД (для формирования callback data).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"admin:name:reject:{user_id}"
                ),
                InlineKeyboardButton(
                    text="✅ Одобрить", callback_data=f"admin:name:approve:{user_id}"
                ),
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
                InlineKeyboardButton(
                    text="⚙️ Переключить мэтчинг",
                    callback_data="admin:toggle_matching_enabled",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓️ День побора", callback_data="admin:update_match_day"
                ),
                InlineKeyboardButton(
                    text="🕐 Время подбора", callback_data="admin:update_match_msk_time"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚖️ Min Jaccard", callback_data="admin:update_min_jaccard"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Кулдаун повторов",
                    callback_data="admin:update_repeat_pair_cooldown_weeks",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏱️ Таймаут ответа",
                    callback_data="admin:update_response_timeout_time",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Интервал напоминаний",
                    callback_data="admin:update_reminder_interval_time",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🧹 Сбросить до заводских настроек",
                    callback_data="admin:clear_selection",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data="admin:cancel_admin_settings"
                ),
                InlineKeyboardButton(
                    text="✅ Сохранить", callback_data="admin:save_admin_settings"
                ),
            ],
        ]
    )


def kb_admin_settings_change_day_of_week(
    current_day_of_week: str,
) -> InlineKeyboardMarkup:
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
                callback_data=f"admin:change_day_of_week:{day_code}",
            )
        )

    keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def kb_admin_settings_change_cooldown_weeks(current_weeks: int) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для выбора кулдауна повторной пары в неделях.

    Args:
        current_weeks (int): текущее значение кулдауна в неделях (1-4).
    """
    keyboard = []
    row = []

    for week in range(1, 5):  # 1-4 недели
        if len(row) == 2:  # 2 кнопки в ряд
            keyboard.append(row)
            row = []

        mark = "✅" if week == current_weeks else "❌"
        # Формируем правильное склонение для "неделя/недели/недель"
        if week == 1:
            week_label = "1 неделя"
        elif 2 <= week <= 4:
            week_label = f"{week} недели"
        else:
            week_label = f"{week} недель"

        row.append(
            InlineKeyboardButton(
                text=f"{mark} {week_label}",
                callback_data=f"admin:change_cooldown_weeks:{week}",
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
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data="admin:back_to_menu"
                ),
            ]
        ]
    )


def kb_admin_users(
    page: int, total_users: int, filters: dict[str, bool]
) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для просмотра списка пользователей.
    """
    pages = max((total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE, 1)
    kb = InlineKeyboardBuilder()

    # Первая строка: Пагинация
    pagination_row = []

    if page > 1:
        pagination_row.append(
            InlineKeyboardButton(
                text="◀️ Назад", callback_data=f"admin:users:{page - 1}"
            )
        )

    pagination_row.append(
        InlineKeyboardButton(text=f"{page}/{pages}", callback_data="admin:users:noop")
    )

    if page < pages:
        pagination_row.append(
            InlineKeyboardButton(
                text="Вперёд ▶️", callback_data=f"admin:users:{page + 1}"
            )
        )

    # Эта строка автоматически будет из 2 или из 3 кнопок
    kb.row(*pagination_row)

    # Вторая строка: Фильтры
    active_state = "✅" if filters.get("active") else "❌"
    blocked_state = "✅" if filters.get("blocked") else "❌"

    kb.row(
        InlineKeyboardButton(
            text=f"{active_state} Активные", callback_data="admin:users:filter:active"
        ),
        InlineKeyboardButton(
            text=f"{blocked_state} Заблокированные",
            callback_data="admin:users:filter:blocked",
        ),
    )

    # Третья строка: Поиск (через inline-режим)
    kb.row(
        InlineKeyboardButton(text="🔍 Поиск", switch_inline_query_current_chat="user:")
    )

    # Четвёртая строка: В главное меню
    kb.row(
        InlineKeyboardButton(
            text="⬅️ В главное меню", callback_data="admin:back_to_menu"
        )
    )

    return kb.as_markup()


def kb_admin_user_actions(
    user_id: int,
    *,
    is_blocked: bool,
    is_admin: bool,
) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для действий над пользователем (динамически по состоянию).
    """

    # 1) Блок/разблок
    if is_blocked:
        block_btn = InlineKeyboardButton(
            text="🔓 Разблокировать",
            callback_data=f"admin:unblock:{user_id}",
        )
    else:
        block_btn = InlineKeyboardButton(
            text="🔒 Заблокировать",
            callback_data=f"admin:block:{user_id}",
        )

    # 2) Назначить/лишить прав админа
    if is_admin:
        role_btn = InlineKeyboardButton(
            text="👤 Лишить прав администратора",
            callback_data=f"admin:remove_admin:{user_id}",
        )
    else:
        role_btn = InlineKeyboardButton(
            text="🔄 Назначить администратором",
            callback_data=f"admin:make_admin:{user_id}",
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [block_btn],
            [role_btn],
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню", callback_data="admin:back_to_menu"
                )
            ],
        ]
    )


def kb_complaint_actions(complaint_id: int) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для действий по жалобе.

    Args:
        complaint_id: ID жалобы для формирования callback data.

    Returns:
        InlineKeyboardMarkup: клавиатура с кнопками действий.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔒 Заблокировать",
                    callback_data=f"complaint:block:{complaint_id}",
                ),
                InlineKeyboardButton(
                    text="⚠️ Отправить предупреждение",
                    callback_data=f"complaint:warn:{complaint_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Закрыть",
                    callback_data=f"complaint:close:{complaint_id}",
                ),
            ],
        ]
    )


def kb_complaint_unblock(complaint_id: int) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру с кнопкой разблокировки для жалобы.

    Используется после блокировки пользователя по жалобе.

    Args:
        complaint_id: ID жалобы для формирования callback data.

    Returns:
        InlineKeyboardMarkup: клавиатура с кнопкой разблокировки.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔓 Разблокировать",
                    callback_data=f"complaint:unblock:{complaint_id}",
                ),
            ],
        ]
    )


def kb_complaint_cancel_warning() -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для отмены ввода текста предупреждения.

    Returns:
        InlineKeyboardMarkup: клавиатура с кнопкой отмены.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="complaint:cancel_warning",
                ),
            ],
        ]
    )


def kb_admin_statistics() -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру для просмотра статистики.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="7️⃣ За 7 дней", callback_data="admin:statistics:7_days"
                ),
                InlineKeyboardButton(
                    text="3️⃣0️⃣ За 30 дней", callback_data="admin:statistics:30_days"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓️ За 6 месяцев", callback_data="admin:statistics:6_months"
                ),
                InlineKeyboardButton(
                    text="📆 За всё время", callback_data="admin:statistics:all_time"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📤 Экспорт в Excel-таблицу",
                    callback_data="admin:statistics:export_excel",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📈 Графики за 6 месяцев",
                    callback_data="admin:statistics:6_months_graphs",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back_to_menu")],
        ]
    )
