"""
Вспомогательные функции для чтения настроек матчинга из таблицы settings.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Setting


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


async def _require_setting(session: AsyncSession, key: str) -> str:
    """
    Возвращает значение настройки или выбрасывает исключение, если его нет.
    """
    value = await _get_setting_value(session, key)
    if value is None:
        raise ValueError(f"Настройка '{key}' отсутствует в базе данных.")
    return value


async def get_setting_bool(session: AsyncSession, key: str) -> bool:
    """
    Возвращает булевое значение настройки из таблицы settings.

    Парсит строковое значение настройки как булево (true/1/yes/on/t считаются True).
    """
    value = await _require_setting(session, key)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "t"}


async def get_setting_int(session: AsyncSession, key: str) -> int:
    """
    Возвращает целочисленное значение настройки из таблицы settings.
    """
    value = await _require_setting(session, key)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Настройка '{key}' содержит некорректное целое число") from exc


async def get_setting_float(session: AsyncSession, key: str) -> float:
    """
    Возвращает значение настройки с плавающей точкой из таблицы settings.
    """
    value = await _require_setting(session, key)
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Настройка '{key}' содержит некорректное число") from exc


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
    match_msk_minute: int
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
        matching_enabled=await get_setting_bool(session, "matching_enabled"),
        match_day=await _require_setting(session, "match_day"),
        match_msk_hour=await get_setting_int(session, "match_msk_hour"),
        match_msk_minute=await get_setting_int(session, "match_msk_minute"),
        response_timeout_hours=await get_setting_int(
            session, "response_timeout_hours"
        ),
        reminder_interval_hours=await get_setting_int(
            session, "reminder_interval_hours"
        ),
        repeat_pair_cooldown_weeks=await get_setting_int(
            session,
            "repeat_pair_cooldown_weeks",
        ),
        min_jaccard=await get_setting_float(
            session, "min_jaccard"
        ),
    )

