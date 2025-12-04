"""
Inline-клавиатуры для сценариев матчинга Random Coffee.
"""

from __future__ import annotations

from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.utils import now_msk
from app.services.matching.constants import (
    MATCH_SLOT_CALENDAR_DAYS,
    MATCH_SLOT_TIME_WINDOWS,
)


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


def kb_match_slots_calendar(
    match_id: int, base_date: date | None = None
) -> InlineKeyboardMarkup:
    """
    Генерирует календарь на 14 дней вперёд с кнопками выбора дат.

    Отображает даты в виде кнопок с днём недели и числом, а также кнопки
    «Готово» и «Очистить».

    Args:
        match_id (int): ID матча для формирования callback_data.
        base_date (date | None): базовая дата для отсчёта (по умолчанию текущая дата).

    Returns:
        InlineKeyboardMarkup: inline-клавиатура с календарём и управляющими кнопками.
    """
    base = base_date or now_msk().date()
    rows: list[list[InlineKeyboardButton]] = []
    total_days = MATCH_SLOT_CALENDAR_DAYS
    weekday_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    day_index = 0
    while day_index < total_days:
        row: list[InlineKeyboardButton] = []
        for _ in range(4):
            if day_index >= total_days:
                break
            current = base + timedelta(days=day_index)
            label = f"{weekday_names[current.weekday()]} {current:%d.%m}"
            callback = f"match_slot_date:{match_id}:{current:%Y%m%d}"
            row.append(InlineKeyboardButton(text=label, callback_data=callback))
            day_index += 1
        if row:
            rows.append(row)

    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Готово",
                callback_data=f"match_slot_done:{match_id}",
            ),
            InlineKeyboardButton(
                text="🗑 Очистить",
                callback_data=f"match_slot_clear:{match_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_match_slots_time(
    match_id: int,
    date_str: str,
    selected_slots: set[str],
) -> InlineKeyboardMarkup:
    """
    Генерирует клавиатуру выбора временных интервалов для конкретной даты.

    Отображает все доступные временные окна с отметками выбранных интервалов
    и кнопку «Готово».

    Args:
        match_id (int): ID матча для формирования callback_data.
        date_str (str): дата в формате YYYYMMDD.
        selected_slots (set[str]): множество уже выбранных интервалов (формат "HH:MM-HH:MM").

    Returns:
        InlineKeyboardMarkup: inline-клавиатура с временными интервалами.
    """
    rows: list[list[InlineKeyboardButton]] = []
    buffer: list[InlineKeyboardButton] = []

    for start, end in MATCH_SLOT_TIME_WINDOWS:
        slot_key = f"{start}-{end}"
        is_selected = slot_key in selected_slots
        label = f"{'✅' if is_selected else '▫️'} {start}-{end}"
        callback = (
            f"match_slot_toggle:{match_id}:{date_str}:"
            f"{start.replace(':', '')}-{end.replace(':', '')}"
        )
        buffer.append(
            InlineKeyboardButton(
                text=label,
                callback_data=callback,
            )
        )
        if len(buffer) == 2:
            rows.append(buffer)
            buffer = []

    if buffer:
        rows.append(buffer)

    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Готово",
                callback_data=f"match_slot_done:{match_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
