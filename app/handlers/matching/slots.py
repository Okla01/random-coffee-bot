"""
Обработчики выбора дат и временных слотов для встречи.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Match, User
from app.database.db import get_user_by_tg_id
from app.handlers.fsm import FSMDataKeys
from app.keyboards.kb_matching import (
    kb_match_slots_calendar,
    kb_match_slots_time,
)
from app.services.matching.constants import (
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


@router.callback_query(F.data.startswith("match_slot_date:"))
async def on_match_slot_date(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает выбор даты в календаре выбора слотов.

    Отображает клавиатуру с временными интервалами для выбранной даты.

    Args:
        cq (CallbackQuery): объект callback-запроса от Telegram.
        state (FSMContext): контекст FSM для хранения черновика слотов.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.

    Returns:
        None: ничего не возвращает.
    """
    match_id, date_str = _parse_date_payload(cq.data)

    async with session_factory() as session:
        match, user = await _ensure_slot_access(session, match_id, cq.from_user.id)
        if not match:
            await cq.answer("Матч уже недоступен", show_alert=True)
            return

        await _ensure_slots_loaded(state, session, match_id, user.id)
        selected = await _get_selected_slots(state, match_id, date_str)

    readable_date = datetime.strptime(date_str, "%Y%m%d").strftime("%d.%m.%Y")
    await cq.message.answer(
        f"Дата {readable_date}. Выберите один или несколько интервалов:",
        reply_markup=kb_match_slots_time(match_id, date_str, selected),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("match_slot_toggle:"))
async def on_match_slot_toggle(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает переключение выбора временного интервала.

    Добавляет или удаляет интервал из черновика слотов пользователя и обновляет клавиатуру.

    Args:
        cq (CallbackQuery): объект callback-запроса от Telegram.
        state (FSMContext): контекст FSM для хранения черновика слотов.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.

    Returns:
        None: ничего не возвращает.
    """
    match_id, date_str, slot_code = _parse_toggle_payload(cq.data)

    async with session_factory() as session:
        match, user = await _ensure_slot_access(session, match_id, cq.from_user.id)
        if not match:
            await cq.answer("Матч уже недоступен", show_alert=True)
            return
        await _ensure_slots_loaded(state, session, match_id, user.id)

    slot_human = _decode_slot_code(slot_code)
    if not _is_valid_window(slot_human):
        await cq.answer("Недопустимый интервал", show_alert=True)
        return

    updated = await _toggle_slot(state, match_id, date_str, slot_human)
    await cq.message.edit_reply_markup(
        kb_match_slots_time(match_id, date_str, updated)
    )
    await cq.answer()


@router.callback_query(F.data.startswith("match_slot_clear:"))
async def on_match_slot_clear(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает очистку всех выбранных слотов.

    Удаляет черновик слотов из FSM и возвращает пользователя к календарю.

    Args:
        cq (CallbackQuery): объект callback-запроса от Telegram.
        state (FSMContext): контекст FSM для хранения черновика слотов.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.

    Returns:
        None: ничего не возвращает.
    """
    match_id = int(cq.data.split(":")[1])
    async with session_factory() as session:
        match, user = await _ensure_slot_access(session, match_id, cq.from_user.id)
        if not match:
            await cq.answer("Матч уже недоступен", show_alert=True)
            return

    await _clear_match_slots_draft(state, match_id)
    await cq.message.edit_reply_markup(kb_match_slots_calendar(match_id))
    await cq.answer("Слоты очищены. Выберите даты заново.", show_alert=True)


@router.callback_query(F.data.startswith("match_slot_done:"))
async def on_match_slot_done(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает подтверждение выбора слотов (кнопка «Готово»).

    Сохраняет выбранные слоты в БД, проверяет наличие слотов у партнёра,
    ищет пересечение и переводит матч в соответствующий статус.

    Args:
        cq (CallbackQuery): объект callback-запроса от Telegram.
        state (FSMContext): контекст FSM для хранения черновика слотов.
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.

    Returns:
        None: ничего не возвращает.
    """
    match_id = int(cq.data.split(":")[1])

    async with session_factory() as session:
        match, user = await _ensure_slot_access(session, match_id, cq.from_user.id)
        if not match:
            await cq.answer("Матч уже недоступен", show_alert=True)
            return

        slots_map = await _get_slots_map(state, match_id)
        if not slots_map:
            await cq.answer("Выберите хотя бы один интервал", show_alert=True)
            return

        slot_entries = _make_slot_entries(slots_map)
        if not slot_entries:
            await cq.answer("Выберите хотя бы один интервал", show_alert=True)
            return
        await replace_user_match_slots(session, match.id, user.id, slot_entries)

        partner_id = (
            match.user_b_id if user.id == match.user_a_id else match.user_a_id
        )
        partner_has_slots = await user_has_match_slots(session, match.id, partner_id)

        await session.flush()

        result_status = "saved"
        common_slot: tuple[datetime, datetime] | None = None
        if partner_has_slots:
            common_slot = await find_first_common_slot(session, match)
            if common_slot:
                match.meeting_start_at = common_slot[0]
                match.meeting_end_at = common_slot[1]
                match.status = MATCH_STATUS_WAITING_CONFIRM
                match.last_reminder_at = None
                match.user_a_response = MATCH_USER_RESPONSE_NONE
                match.user_b_response = MATCH_USER_RESPONSE_NONE
                result_status = "waiting_confirm"
            else:
                match.status = MATCH_STATUS_EXPIRED_TIMEOUT
                match.last_reminder_at = None
                result_status = "expired"

        await session.commit()

    await _clear_match_slots_draft(state, match_id)
    await notify_match_slots_saved(cq.bot, user)

    if result_status == "waiting_confirm" and common_slot:
        await notify_waiting_confirm(cq.bot, match, *common_slot)
    elif result_status == "expired":
        await notify_no_common_slot(cq.bot, match)

    await cq.answer()


async def _ensure_slot_access(
    session: AsyncSession,
    match_id: int,
    telegram_id: int,
) -> tuple[Match | None, User | None]:
    """
    Проверяет доступ пользователя к выбору слотов для матча.

    Валидирует, что матч существует, находится в статусе waiting_slots,
    и пользователь является одним из участников.

    Args:
        session (AsyncSession): активная сессия БД.
        match_id (int): ID матча.
        telegram_id (int): Telegram ID пользователя.

    Returns:
        tuple[Match | None, User | None]: кортеж (матч, пользователь) или (None, None)
            если доступ запрещён.
    """
    match = await get_match_with_relations(session, match_id)
    if not match or match.status != MATCH_STATUS_WAITING_SLOTS:
        return None, None

    user = await get_user_by_tg_id(session, telegram_id)
    if not user:
        return None, None
    if user.id not in {match.user_a_id, match.user_b_id}:
        return None, None
    return match, user


async def _ensure_slots_loaded(
    state: FSMContext,
    session: AsyncSession,
    match_id: int,
    user_id: int,
) -> None:
    """
    Загружает существующие слоты пользователя в FSM, если они ещё не загружены.

    Args:
        state (FSMContext): контекст FSM для хранения черновика слотов.
        session (AsyncSession): активная сессия БД.
        match_id (int): ID матча.
        user_id (int): ID пользователя.

    Returns:
        None: ничего не возвращает.
    """
    data = await state.get_data()
    drafts = data.get(FSMDataKeys.MATCH_SLOTS_DRAFT.value) or {}
    match_key = str(match_id)
    if match_key in drafts:
        return

    slots_map: dict[str, list[str]] = {}
    slots = await load_user_match_slots(session, match_id, user_id)
    for slot in slots:
        date_str = slot.date.strftime("%Y%m%d")
        slots_map.setdefault(date_str, []).append(f"{slot.time_from}-{slot.time_to}")
    drafts[match_key] = {"slots": slots_map}
    await state.update_data(**{FSMDataKeys.MATCH_SLOTS_DRAFT.value: drafts})


async def _get_selected_slots(
    state: FSMContext,
    match_id: int,
    date_str: str,
) -> set[str]:
    """
    Получает множество выбранных слотов для указанной даты из FSM.

    Args:
        state (FSMContext): контекст FSM для хранения черновика слотов.
        match_id (int): ID матча.
        date_str (str): дата в формате YYYYMMDD.

    Returns:
        set[str]: множество выбранных интервалов (формат "HH:MM-HH:MM").
    """
    slots_map = await _get_slots_map(state, match_id)
    return set(slots_map.get(date_str, []))


async def _toggle_slot(
    state: FSMContext,
    match_id: int,
    date_str: str,
    slot: str,
) -> set[str]:
    """
    Переключает выбор временного интервала для даты (добавляет или удаляет).

    Args:
        state (FSMContext): контекст FSM для хранения черновика слотов.
        match_id (int): ID матча.
        date_str (str): дата в формате YYYYMMDD.
        slot (str): интервал в формате "HH:MM-HH:MM".

    Returns:
        set[str]: обновлённое множество выбранных интервалов для даты.
    """
    slots_map = await _get_slots_map(state, match_id)
    selected = set(slots_map.get(date_str, []))
    if slot in selected:
        selected.remove(slot)
    else:
        selected.add(slot)
    slots_map[date_str] = sorted(selected)
    await _store_slots_map(state, match_id, slots_map)
    return set(slots_map[date_str])


async def _get_slots_map(state: FSMContext, match_id: int) -> dict[str, list[str]]:
    """
    Получает словарь выбранных слотов из FSM для матча.

    Args:
        state (FSMContext): контекст FSM для хранения черновика слотов.
        match_id (int): ID матча.

    Returns:
        dict[str, list[str]]: словарь {дата: [список интервалов]}, где дата в формате YYYYMMDD.
    """
    data = await state.get_data()
    drafts = data.get(FSMDataKeys.MATCH_SLOTS_DRAFT.value) or {}
    match_key = str(match_id)
    draft = drafts.setdefault(match_key, {"slots": {}})
    return draft["slots"]


async def _store_slots_map(
    state: FSMContext,
    match_id: int,
    slots_map: dict[str, list[str]],
) -> None:
    """
    Сохраняет словарь выбранных слотов в FSM для матча.

    Args:
        state (FSMContext): контекст FSM для хранения черновика слотов.
        match_id (int): ID матча.
        slots_map (dict[str, list[str]]): словарь {дата: [список интервалов]}.

    Returns:
        None: ничего не возвращает.
    """
    data = await state.get_data()
    drafts = data.get(FSMDataKeys.MATCH_SLOTS_DRAFT.value) or {}
    drafts[str(match_id)] = {"slots": slots_map}
    await state.update_data(**{FSMDataKeys.MATCH_SLOTS_DRAFT.value: drafts})


async def _clear_match_slots_draft(state: FSMContext, match_id: int) -> None:
    """
    Удаляет черновик слотов для матча из FSM.

    Args:
        state (FSMContext): контекст FSM для хранения черновика слотов.
        match_id (int): ID матча.

    Returns:
        None: ничего не возвращает.
    """
    data = await state.get_data()
    drafts = data.get(FSMDataKeys.MATCH_SLOTS_DRAFT.value) or {}
    if str(match_id) in drafts:
        drafts.pop(str(match_id))
        await state.update_data(**{FSMDataKeys.MATCH_SLOTS_DRAFT.value: drafts})


def _parse_date_payload(data: str) -> tuple[int, str]:
    """
    Парсит callback_data для выбора даты.

    Args:
        data (str): callback_data в формате "match_slot_date:match_id:YYYYMMDD".

    Returns:
        tuple[int, str]: кортеж (match_id, date_str).
    """
    _, match_id, date_str = data.split(":")
    return int(match_id), date_str


def _parse_toggle_payload(data: str) -> tuple[int, str, str]:
    """
    Парсит callback_data для переключения слота.

    Args:
        data (str): callback_data в формате "match_slot_toggle:match_id:YYYYMMDD:HHMM-HHMM".

    Returns:
        tuple[int, str, str]: кортеж (match_id, date_str, slot_code).
    """
    _, match_id, date_str, slot_code = data.split(":")
    return int(match_id), date_str, slot_code


def _decode_slot_code(code: str) -> str:
    """
    Декодирует закодированный интервал времени в читаемый формат.

    Args:
        code (str): закодированный интервал в формате "HHMM-HHMM".

    Returns:
        str: интервал в формате "HH:MM-HH:MM".
    """
    start_raw, end_raw = code.split("-")
    start = f"{start_raw[:2]}:{start_raw[2:]}"
    end = f"{end_raw[:2]}:{end_raw[2:]}"
    return f"{start}-{end}"


def _is_valid_window(slot: str) -> bool:
    """
    Проверяет, является ли интервал допустимым временным окном.

    Args:
        slot (str): интервал в формате "HH:MM-HH:MM".

    Returns:
        bool: True если интервал присутствует в списке допустимых окон, иначе False.
    """
    start, end = slot.split("-")
    return (start, end) in MATCH_SLOT_TIME_WINDOWS


def _make_slot_entries(slots_map: dict[str, list[str]]) -> list[SlotEntry]:
    """
    Преобразует словарь слотов из FSM в список объектов SlotEntry.

    Args:
        slots_map (dict[str, list[str]]): словарь {дата: [список интервалов]},
            где дата в формате YYYYMMDD, интервалы в формате "HH:MM-HH:MM".

    Returns:
        list[SlotEntry]: список объектов SlotEntry для сохранения в БД.
    """
    entries: list[SlotEntry] = []
    for date_str, slots in slots_map.items():
        match_date = datetime.strptime(date_str, "%Y%m%d").date()
        for slot in slots:
            start, end = slot.split("-")
            entries.append(SlotEntry(match_date=match_date, time_from=start, time_to=end))
    return entries

