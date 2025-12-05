"""
Вспомогательные функции для домена матчинга: проверки активных матчей и расчёт Jaccard.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Match
from .constants import MATCH_ACTIVE_STATUSES


async def user_has_active_match(session: AsyncSession, user_id: int) -> bool:
    """
    Проверяет, есть ли у пользователя активный матч.

    Активными считаются матчи со статусами: pending_response, waiting_slots,
    waiting_confirm, scheduled.

    Args:
        session (AsyncSession): активная сессия БД.
        user_id (int): ID пользователя для проверки.

    Returns:
        bool: True если у пользователя есть активный матч, иначе False.
    """
    stmt = (
        select(Match.id)
        .where(
            or_(Match.user_a_id == user_id, Match.user_b_id == user_id),
            Match.status.in_(MATCH_ACTIVE_STATUSES),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


def compute_jaccard(
    interests_a: Iterable[str] | None,
    interests_b: Iterable[str] | None,
) -> float:
    """
    Вычисляет коэффициент Жаккара для двух наборов интересов.

    Коэффициент Жаккара = |пересечение| / |объединение|.
    Используется для оценки схожести интересов двух пользователей.

    Args:
        interests_a (Iterable[str] | None): первый набор интересов.
        interests_b (Iterable[str] | None): второй набор интересов.

    Returns:
        float: коэффициент Жаккара в диапазоне [0.0, 1.0]. Возвращает 0.0,
            если один из наборов пуст или оба пусты.
    """
    set_a = _interests_to_set(interests_a)
    set_b = _interests_to_set(interests_b)

    if not set_a or not set_b:
        return 0.0

    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def extract_interests_list(raw: object) -> list[str]:
    """
    Извлекает и нормализует список интересов из JSON-поля пользователя.

    Поддерживает различные форматы хранения интересов:
    - {"interests": [...]} (стандартный формат)
    - прямой список/массив
    - другие словарные структуры

    Args:
        raw (object): исходное значение из interests_json (dict, list, None и т.д.).

    Returns:
        list[str]: список нормализованных (lowercase, trimmed) интересов.
    """
    if raw is None:
        return []

    if isinstance(raw, dict):
        # стандартный формат {"interests": [...]}
        interests = raw.get("interests")
        if isinstance(interests, (list, tuple)):
            return [
                value
                for value in (_normalize_interest(item) for item in interests)
                if value
            ]
        # fallback — собираем все массивы
        values: list[str] = []
        for value in raw.values():
            if isinstance(value, (list, tuple)):
                values.extend(_normalize_interest(item) for item in value if item)
        return [item for item in values if item]

    if isinstance(raw, (list, tuple)):
        return [value for value in (_normalize_interest(item) for item in raw) if value]

    return []


def _interests_to_set(values: Iterable[str] | None) -> set[str]:
    """
    Преобразует итератор интересов в нормализованное множество.

    Args:
        values (Iterable[str] | None): исходные интересы.

    Returns:
        set[str]: множество нормализованных (lowercase, trimmed) интересов.
    """
    if not values:
        return set()
    normalized = {_normalize_interest(value) for value in values if value}
    return {value for value in normalized if value}


def _normalize_interest(value: object) -> str:
    """
    Нормализует одно значение интереса (trim + lowercase).

    Args:
        value (object): исходное значение интереса.

    Returns:
        str: нормализованная строка (пустая, если value is None).
    """
    if value is None:
        return ""
    return str(value).strip().lower()
