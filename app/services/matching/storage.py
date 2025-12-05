"""
Утилиты доступа к данным матчей.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import Match, MatchSlot, User
from app.database.utils import MOSCOW_TZ


@dataclass(frozen=True)
class SlotEntry:
    """
    Представление одного временного слота для встречи.

    Attributes:
        match_date (date): дата встречи.
        time_from (str): время начала в формате HH:MM.
        time_to (str): время окончания в формате HH:MM.
    """

    match_date: date
    time_from: str  # формат HH:MM
    time_to: str  # формат HH:MM


async def get_match_with_relations(
    session: AsyncSession, match_id: int
) -> Match | None:
    """
    Загружает матч с предзагруженными связанными пользователями.

    Args:
        session (AsyncSession): активная сессия БД.
        match_id (int): ID матча.

    Returns:
        Match | None: объект матча с загруженными user_a и user_b, или None если не найден.
    """
    stmt = (
        select(Match)
        .options(
            selectinload(Match.user_a),
            selectinload(Match.user_b),
        )
        .where(Match.id == match_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def set_match_response(
    session: AsyncSession,
    match: Match,
    user: User,
    response: str,
) -> bool:
    """
    Обновляет поле ответа участника матча (user_a_response или user_b_response).

    Args:
        session (AsyncSession): активная сессия БД.
        match (Match): объект матча.
        user (User): пользователь, чей ответ обновляется.
        response (str): значение ответа (ready, skip, confirm, none).

    Returns:
        bool: True если пользователь является участником матча и ответ обновлён,
            False если пользователь не участвует в матче.
    """
    if user.id == match.user_a_id:
        match.user_a_response = response
    elif user.id == match.user_b_id:
        match.user_b_response = response
    else:
        return False
    await session.flush()
    return True


async def load_user_match_slots(
    session: AsyncSession, match_id: int, user_id: int
) -> list[MatchSlot]:
    """
    Загружает все временные слоты пользователя для указанного матча.

    Args:
        session (AsyncSession): активная сессия БД.
        match_id (int): ID матча.
        user_id (int): ID пользователя.

    Returns:
        list[MatchSlot]: список слотов, отсортированный по дате и времени начала.
    """
    stmt = (
        select(MatchSlot)
        .where(
            MatchSlot.match_id == match_id,
            MatchSlot.user_id == user_id,
        )
        .order_by(MatchSlot.date, MatchSlot.time_from)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def replace_user_match_slots(
    session: AsyncSession,
    match_id: int,
    user_id: int,
    slots: Iterable[SlotEntry],
) -> None:
    """
    Заменяет все временные слоты пользователя для матча на новые.

    Удаляет все существующие слоты пользователя для данного матча и создаёт новые.
    Операция выполняется атомарно в рамках одной транзакции.

    Args:
        session (AsyncSession): активная сессия БД.
        match_id (int): ID матча.
        user_id (int): ID пользователя.
        slots (Iterable[SlotEntry]): новые слоты для замены.

    Returns:
        None: ничего не возвращает.
    """
    await session.execute(
        delete(MatchSlot).where(
            MatchSlot.match_id == match_id,
            MatchSlot.user_id == user_id,
        )
    )
    objects: list[MatchSlot] = []
    for slot in slots:
        slot_dt = datetime.combine(slot.match_date, dt_time.min, tzinfo=MOSCOW_TZ)
        objects.append(
            MatchSlot(
                match_id=match_id,
                user_id=user_id,
                date=slot_dt,
                time_from=slot.time_from,
                time_to=slot.time_to,
            )
        )
    if objects:
        session.add_all(objects)
    await session.flush()


async def user_has_match_slots(
    session: AsyncSession, match_id: int, user_id: int
) -> bool:
    """
    Проверяет, есть ли у пользователя сохранённые слоты для матча.

    Args:
        session (AsyncSession): активная сессия БД.
        match_id (int): ID матча.
        user_id (int): ID пользователя.

    Returns:
        bool: True если у пользователя есть хотя бы один слот, иначе False.
    """
    stmt = (
        select(MatchSlot.id)
        .where(
            MatchSlot.match_id == match_id,
            MatchSlot.user_id == user_id,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def find_first_common_slot(
    session: AsyncSession,
    match: Match,
) -> tuple[datetime, datetime] | None:
    """
    Находит первое пересечение временных слотов двух участников матча.

    Сравнивает все слоты user_a и user_b, ищет пересечения по дате и времени,
    возвращает самое раннее пересечение (по дате и времени начала).

    Args:
        session (AsyncSession): активная сессия БД.
        match (Match): объект матча с загруженными user_a и user_b.

    Returns:
        tuple[datetime, datetime] | None: кортеж (начало, конец) первого общего интервала
            в МСК, или None если пересечений нет.
    """
    if not match.user_a_id or not match.user_b_id:
        return None

    slots_a = await load_user_match_slots(session, match.id, match.user_a_id)
    slots_b = await load_user_match_slots(session, match.id, match.user_b_id)

    intersections: list[tuple[datetime, datetime]] = []
    for slot_a in slots_a:
        for slot_b in slots_b:
            if slot_a.date.date() != slot_b.date.date():
                continue
            start_str = max(slot_a.time_from, slot_b.time_from)
            end_str = min(slot_a.time_to, slot_b.time_to)
            if start_str >= end_str:
                continue
            start_dt = datetime.combine(
                slot_a.date.date(),
                _parse_time(start_str),
                tzinfo=MOSCOW_TZ,
            )
            end_dt = datetime.combine(
                slot_a.date.date(),
                _parse_time(end_str),
                tzinfo=MOSCOW_TZ,
            )
            intersections.append((start_dt, end_dt))

    if not intersections:
        return None
    intersections.sort(key=lambda item: item[0])
    return intersections[0]


def _parse_time(value: str) -> dt_time:
    """
    Парсит строку времени формата HH:MM в объект time.

    Args:
        value (str): строка времени в формате HH:MM.

    Returns:
        dt_time: объект time с соответствующими часами и минутами.
    """
    hours, minutes = value.split(":")
    return dt_time(int(hours), int(minutes))


async def clear_match_slots(session: AsyncSession, match_id: int) -> None:
    """
    Удаляет все временные слоты для указанного матча.

    Args:
        session (AsyncSession): активная сессия БД.
        match_id (int): ID матча.

    Returns:
        None: ничего не возвращает.
    """
    await session.execute(delete(MatchSlot).where(MatchSlot.match_id == match_id))
    await session.flush()


async def cleanup_inactive_match(
    session: AsyncSession,
    match: Match,
) -> None:
    """
    Очищает данные матча при переходе в неактивный статус.

    Обнуляет поля last_message_id_a и last_message_id_b, а также удаляет
    все выбранные временные слоты пользователей для данного матча.

    Args:
        session (AsyncSession): активная сессия БД.
        match (Match): объект матча.

    Returns:
        None: ничего не возвращает.
    """
    # Обнуляем ID последних сообщений
    match.last_message_id_a = None
    match.last_message_id_b = None

    # Удаляем все временные слоты для данного матча
    await clear_match_slots(session, match.id)
