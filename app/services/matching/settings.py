"""
Вспомогательные функции для чтения настроек матчинга из таблицы settings.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Setting
from .constants import (
    DEFAULT_MIN_JACCARD,
    DEFAULT_REMINDER_INTERVAL_HOURS,
    DEFAULT_REPEAT_PAIR_COOLDOWN_WEEKS,
    DEFAULT_RESPONSE_TIMEOUT_HOURS,
)


async def _get_setting_value(session: AsyncSession, key: str) -> str | None:
    """
    Получает строковое значение настройки по ключу.

    Args:
        session (AsyncSession): активная сессия БД.
        key (str): ключ настройки.

    Returns:
        str | None: значение настройки или None, если настройка не найдена.
    """
    result = await session.execute(
        select(Setting.value).where(Setting.key == key)
    )
    return result.scalar_one_or_none()


async def get_setting_bool(
    session: AsyncSession, key: str, default: bool = False
) -> bool:
    """
    Возвращает булевое значение настройки из таблицы settings.

    Парсит строковое значение настройки как булево (true/1/yes/on/t считаются True).

    Args:
        session (AsyncSession): активная сессия БД.
        key (str): ключ настройки.
        default (bool): значение по умолчанию, если настройка не найдена.

    Returns:
        bool: булево значение настройки или default.
    """
    value = await _get_setting_value(session, key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "t"}


async def get_setting_int(
    session: AsyncSession, key: str, default: int = 0
) -> int:
    """
    Возвращает целочисленное значение настройки из таблицы settings.

    Парсит строковое значение настройки как целое число.

    Args:
        session (AsyncSession): активная сессия БД.
        key (str): ключ настройки.
        default (int): значение по умолчанию, если настройка не найдена или не парсится.

    Returns:
        int: целочисленное значение настройки или default.
    """
    value = await _get_setting_value(session, key)
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


async def get_setting_float(
    session: AsyncSession, key: str, default: float = 0.0
) -> float:
    """
    Возвращает значение настройки с плавающей точкой из таблицы settings.

    Парсит строковое значение настройки как float (запятая заменяется на точку).

    Args:
        session (AsyncSession): активная сессия БД.
        key (str): ключ настройки.
        default (float): значение по умолчанию, если настройка не найдена или не парсится.

    Returns:
        float: значение настройки с плавающей точкой или default.
    """
    value = await _get_setting_value(session, key)
    if value is None:
        return default
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class MatchingSettings:
    """
    Типизированные настройки матчинга Random Coffee.

    Содержит все параметры, необходимые для работы алгоритма подбора пар
    и управления жизненным циклом матчей.
    """

    matching_enabled: bool
    match_day: str
    match_msk_hour: int
    response_timeout_hours: int
    reminder_interval_hours: int
    repeat_pair_cooldown_weeks: int
    min_jaccard: float


async def load_matching_settings(session: AsyncSession) -> MatchingSettings:
    """
    Загружает и типизирует ключевые настройки матчинга из таблицы settings.

    Читает все необходимые настройки из БД и возвращает их в виде типизированного объекта.
    Использует значения по умолчанию, если настройки отсутствуют.

    Args:
        session (AsyncSession): активная сессия БД.

    Returns:
        MatchingSettings: объект с загруженными настройками матчинга.
    """
    return MatchingSettings(
        matching_enabled=await get_setting_bool(session, "matching_enabled", True),
        match_day=(await _get_setting_value(session, "match_day")) or "fri",
        match_msk_hour=await get_setting_int(session, "match_msk_hour", 12),
        response_timeout_hours=await get_setting_int(
            session, "response_timeout_hours", DEFAULT_RESPONSE_TIMEOUT_HOURS
        ),
        reminder_interval_hours=await get_setting_int(
            session, "reminder_interval_hours", DEFAULT_REMINDER_INTERVAL_HOURS
        ),
        repeat_pair_cooldown_weeks=await get_setting_int(
            session,
            "repeat_pair_cooldown_weeks",
            DEFAULT_REPEAT_PAIR_COOLDOWN_WEEKS,
        ),
        min_jaccard=await get_setting_float(
            session, "min_jaccard", DEFAULT_MIN_JACCARD
        ),
    )

