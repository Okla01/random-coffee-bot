"""
Диалог выбора дат и временных слотов для встречи (aiogram_dialog).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.fsm.state import State, StatesGroup
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.kbd import Button, Group, Select
from aiogram_dialog.widgets.text import Const, Format
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Match, User
from app.database.db import get_user_by_tg_id
from app.database.utils import now_msk
from app.services.matching.constants import (
    MATCH_SLOT_CALENDAR_DAYS,
    MATCH_SLOT_TIME_WINDOWS,
    MATCH_STATUS_EXPIRED_TIMEOUT,
    MATCH_STATUS_WAITING_CONFIRM,
    MATCH_STATUS_WAITING_SLOTS,
    MATCH_USER_RESPONSE_NONE,
)
from app.services.matching.messages import (
    notify_match_slots_saved,
    notify_no_common_slot,
    notify_waiting_confirm,
)
from app.services.matching.storage import (
    SlotEntry,
    find_first_common_slot,
    get_match_with_relations,
    load_user_match_slots,
    replace_user_match_slots,
    user_has_match_slots,
)

router = Router()


class MatchSlotsDialogSG(StatesGroup):
    """Состояния диалога выбора календаря и времени."""

    calendar = State()
    time = State()


class GridSelect(Select):
    """Select-виджет, который выводит элементы в несколько колонок."""

    def __init__(self, *args, columns: int = 2, **kwargs):
        super().__init__(*args, **kwargs)
        self.columns = max(1, columns)

    async def _render_keyboard(
        self,
        data: dict,
        manager: DialogManager,
    ):
        items = list(self.items_getter(data))
        keyboard: list[list] = []
        row: list = []
        for pos, item in enumerate(items):
            row.append(await self._render_button(pos, item, item, data, manager))
            if len(row) == self.columns:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return keyboard


async def _dialog_on_start(start_data: dict, manager: DialogManager) -> None:
    """
    Инициализирует данные диалога (подгружает уже выбранные слоты).

    Args:
        start_data (dict): данные, переданные при запуске диалога.
        manager (DialogManager): менеджер aiogram_dialog.
    """
    match_id = int(start_data["match_id"])
    user_id = int(start_data["user_id"])
    session_factory: async_sessionmaker[AsyncSession] = manager.middleware_data[
        "session_factory"
    ]

    async with session_factory() as session:
        slots_map = await _load_user_slots_map(session, match_id, user_id)

    manager.dialog_data.update(
        match_id=match_id,
        user_id=user_id,
        slots_map=slots_map,
        current_date=None,
    )


async def _calendar_getter(dialog_manager: DialogManager, **_) -> dict:
    """
    Формирует данные для окна календаря.

    Args:
        dialog_manager (DialogManager): менеджер aiogram_dialog.

    Returns:
        dict: данные для шаблонов окна календаря.
    """
    slots_map = dialog_manager.dialog_data.get("slots_map", {})
    current_date = dialog_manager.dialog_data.get("current_date")
    summary = _format_slots_summary(slots_map)
    day_items = _build_day_items(slots_map, current_date)
    return {
        "slots_summary": summary or "Пока ничего не выбрано.",
        "has_any_slots": bool(slots_map),
        "day_items": day_items,
    }


async def _time_getter(dialog_manager: DialogManager, **_) -> dict:
    """
    Формирует данные для окна выбора временных интервалов.

    Args:
        dialog_manager (DialogManager): менеджер aiogram_dialog.

    Returns:
        dict: данные для шаблонов окна выбора времени.
    """
    date_str: str | None = dialog_manager.dialog_data.get("current_date")
    slots_map = dialog_manager.dialog_data.get("slots_map", {})
    selected = set(slots_map.get(date_str, [])) if date_str else set()
    items = []
    for start, end in MATCH_SLOT_TIME_WINDOWS:
        slot_id = f"{start}-{end}"
        items.append(
            {
                "id": slot_id,
                "label": f"{'✅' if slot_id in selected else '▫️'} {start}-{end}",
            }
        )
    return {
        "current_date_caption": _format_date_caption(date_str),
        "time_windows": items,
        "has_time_selection": bool(selected),
        "has_any_slots": bool(slots_map),
        "current_date_selected": date_str is not None,
    }


async def _on_day_selected(
    callback: CallbackQuery,
    widget: GridSelect,
    manager: DialogManager,
    date_str: str,
) -> None:
    """
    Обрабатывает выбор даты из списка ближайших 14 дней.
    """
    slots_map = manager.dialog_data.setdefault("slots_map", {})
    slots_map.setdefault(date_str, [])
    manager.dialog_data["current_date"] = date_str
    await manager.switch_to(MatchSlotsDialogSG.time)
    await callback.answer()


async def _on_time_toggle(
    callback: CallbackQuery,
    widget: Select,
    manager: DialogManager,
    item_id: str,
) -> None:
    """
    Переключает временной интервал в списке выбранных.

    Args:
        callback (CallbackQuery): событие выбора времени.
        widget (ManagedSelect): select-виджет временных окон.
        manager (DialogManager): менеджер aiogram_dialog.
        item_id (str): идентификатор интервала (HH:MM-HH:MM).
    """
    date_str = manager.dialog_data.get("current_date")
    if not date_str:
        await callback.answer("Сначала выберите дату.", show_alert=True)
        return
    slots_map = manager.dialog_data.setdefault("slots_map", {})
    selected = set(slots_map.get(date_str, []))
    if item_id in selected:
        selected.remove(item_id)
    else:
        selected.add(item_id)
    slots_map[date_str] = sorted(selected)
    await manager.switch_to(MatchSlotsDialogSG.time)


async def _on_back_to_calendar(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
) -> None:
    """
    Возвращает пользователя к окну календаря.
    """
    await manager.switch_to(MatchSlotsDialogSG.calendar)
    await callback.answer()


async def _on_clear_all(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
) -> None:
    """
    Очищает все выбранные даты и интервалы.
    """
    manager.dialog_data["slots_map"] = {}
    manager.dialog_data["current_date"] = None
    await manager.switch_to(MatchSlotsDialogSG.calendar)
    await callback.answer("Выбор очищен.", show_alert=True)


async def _on_clear_date(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
) -> None:
    """
    Очищает выбор интервалов для текущей даты.
    """
    date_str = manager.dialog_data.get("current_date")
    if not date_str:
        await callback.answer("Дата не выбрана.", show_alert=True)
        return
    slots_map = manager.dialog_data.setdefault("slots_map", {})
    slots_map.pop(date_str, None)
    manager.dialog_data["current_date"] = None
    await manager.switch_to(MatchSlotsDialogSG.calendar)
    await callback.answer("Слоты для даты очищены.", show_alert=True)


async def _on_save_slots(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
) -> None:
    """
    Сохраняет выбранные слоты и пытается найти пересечение с партнёром.
    """
    slots_map: dict[str, list[str]] = manager.dialog_data.get("slots_map", {})
    if not _has_slots(slots_map):
        await callback.answer("Выберите хотя бы один интервал.", show_alert=True)
        return

    match_id = int(manager.dialog_data["match_id"])
    user_id = int(manager.dialog_data["user_id"])
    session_factory: async_sessionmaker[AsyncSession] = manager.middleware_data[
        "session_factory"
    ]

    async with session_factory() as session:
        match = await get_match_with_relations(session, match_id)
        if not match or match.status != MATCH_STATUS_WAITING_SLOTS:
            await callback.answer("Матч уже недоступен.", show_alert=True)
            await manager.done()
            return
        user = await session.get(User, user_id)
        if not user:
            await callback.answer("Обновите регистрацию.", show_alert=True)
            await manager.done()
            return

        slot_entries = _make_slot_entries(slots_map)
        if not slot_entries:
            await callback.answer("Выберите хотя бы один интервал.", show_alert=True)
            return

        await replace_user_match_slots(session, match.id, user.id, slot_entries)
        partner_id = match.user_b_id if user.id == match.user_a_id else match.user_a_id
        partner_has_slots = await user_has_match_slots(session, match.id, partner_id)

        result_status = "saved"
        common_slot: tuple[datetime, datetime] | None = None
        confirm_message_ids: dict[int, int] | None = None
        if partner_has_slots:
            common_slot = await find_first_common_slot(session, match)
            if common_slot:
                match.meeting_start_at, match.meeting_end_at = common_slot
                match.status = MATCH_STATUS_WAITING_CONFIRM
                match.last_reminder_at = None
                match.user_a_response = MATCH_USER_RESPONSE_NONE
                match.user_b_response = MATCH_USER_RESPONSE_NONE
                result_status = "waiting_confirm"
                confirm_message_ids = await notify_waiting_confirm(
                    callback.bot,
                    match,
                    *common_slot,
                )
                if match.user_a_id:
                    match.last_message_id_a = confirm_message_ids.get(match.user_a_id)
                if match.user_b_id:
                    match.last_message_id_b = confirm_message_ids.get(match.user_b_id)
            else:
                match.status = MATCH_STATUS_EXPIRED_TIMEOUT
                match.last_reminder_at = None
                result_status = "expired"

        await session.commit()

    await manager.done()
    if result_status == "saved":
        await notify_match_slots_saved(callback.bot, user)
    elif result_status == "expired":
        await notify_no_common_slot(callback.bot, match)


match_slots_dialog = Dialog(
    Window(
        Const(
            "Выберите дату из ближайших 14 дней и укажите временные интервалы."
        ),
        Format("{slots_summary}"),
        GridSelect(
            Format("{item[label]}"),
            id="day_select",
            item_id_getter=lambda item: item["id"],
            items="day_items",
            on_click=_on_day_selected,
            columns=2,
        ),
        Group(
            Button(
                Const("🗑 Очистить всё"),
                id="clear_all",
                on_click=_on_clear_all,
                when="has_any_slots",
            ),
            Button(
                Const("✅ Сохранить слоты"),
                id="save_slots_calendar",
                on_click=_on_save_slots,
                when="has_any_slots",
            ),
            width=2,
        ),
        state=MatchSlotsDialogSG.calendar,
        getter=_calendar_getter,
    ),
    Window(
        Format("Дата: {current_date_caption}\nОтметьте подходящие интервалы:"),
        GridSelect(
            Format("{item[label]}"),
            id="time_select",
            item_id_getter=lambda item: item["id"],
            items="time_windows",
            on_click=_on_time_toggle,
            columns=3,
        ),
        Group(
            Button(
                Const("🗑 Очистить интервалы"),
                id="clear_date",
                on_click=_on_clear_date,
                when="current_date_selected",
            ),
            width=1,
        ),
        Button(
            Const("⬅️ Назад к календарю"),
            id="back_calendar",
            on_click=_on_back_to_calendar,
        ),
        state=MatchSlotsDialogSG.time,
        getter=_time_getter,
    ),
    on_start=_dialog_on_start,
)

router.include_router(match_slots_dialog)


async def _ensure_slot_access(
    session: AsyncSession,
    match_id: int,
    telegram_id: int,
) -> tuple[Match | None, User | None]:
    """
    Проверяет, может ли пользователь выбирать слоты для матча.
    """
    match = await get_match_with_relations(session, match_id)
    if not match or match.status != MATCH_STATUS_WAITING_SLOTS:
        return None, None
    user = await get_user_by_tg_id(session, telegram_id)
    if not user or user.id not in {match.user_a_id, match.user_b_id}:
        return None, None
    return match, user


async def _load_user_slots_map(
    session: AsyncSession, match_id: int, user_id: int
) -> dict[str, list[str]]:
    """
    Загружает выбранные слоты пользователя из БД.
    """
    slots = await load_user_match_slots(session, match_id, user_id)
    slots_map: dict[str, list[str]] = {}
    for slot in slots:
        date_str = slot.date.strftime("%Y%m%d")
        slots_map.setdefault(date_str, []).append(f"{slot.time_from}-{slot.time_to}")
    for values in slots_map.values():
        values.sort()
    return slots_map


def _format_slots_summary(slots_map: dict[str, list[str]]) -> str:
    """
    Форматирует выбранные слоты в текст для вывода в календаре.
    """
    if not slots_map:
        return ""
    lines: list[str] = []
    for date_str in sorted(slots_map.keys()):
        caption = _format_date_caption(date_str)
        times = ", ".join(slots_map[date_str])
        lines.append(f"{caption}: {times}")
    return "\n".join(lines)


def _build_day_items(
    slots_map: dict[str, list[str]],
    current_date: str | None,
) -> list[dict]:
    """
    Формирует список ближайших 14 дней для отображения в клавиатуре.
    """
    base = now_msk().date()
    weekday_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    items: list[dict] = []
    for offset in range(MATCH_SLOT_CALENDAR_DAYS):
        dt = base + timedelta(days=offset)
        date_str = dt.strftime("%Y%m%d")
        label = f"{weekday_names[dt.weekday()]} {dt:%d.%m}"
        if slots_map.get(date_str):
            label += " •"
        if date_str == current_date:
            label = f"👉 {label}"
        items.append({"id": date_str, "label": label})
    return items


def _format_date_caption(date_str: str | None) -> str:
    """
    Возвращает человекочитаемое представление даты.
    """
    if not date_str:
        return "не выбрана"
    dt = datetime.strptime(date_str, "%Y%m%d").date()
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return f"{weekdays[dt.weekday()]} {dt:%d.%m}"


def _has_slots(slots_map: dict[str, list[str]]) -> bool:
    """
    Проверяет, есть ли в словаре хотя бы один выбранный интервал.
    """
    return any(slots_map.values())


def _make_slot_entries(slots_map: dict[str, list[str]]) -> list[SlotEntry]:
    """
    Конвертирует словарь выбранных слотов в список SlotEntry для БД.
    """
    entries: list[SlotEntry] = []
    for date_str, slots in slots_map.items():
        match_date = datetime.strptime(date_str, "%Y%m%d").date()
        for slot in slots:
            start, end = slot.split("-")
            entries.append(
                SlotEntry(match_date=match_date, time_from=start, time_to=end)
            )
    return entries

