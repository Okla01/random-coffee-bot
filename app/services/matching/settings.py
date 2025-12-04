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


def parse_time_to_hours_minutes(time_str: str) -> tuple[int, int] | None:
    """
    Парсит время в формате ЧЧ:ММ на час и минуты.

    Args:
        time_str: строка в формате "ЧЧ:ММ"

    Returns:
        tuple[int, int] | None: кортеж (час, минуты) или None при ошибке
    """
    if ":" not in time_str:
        return None
    
    parts = time_str.split(":")
    if len(parts) != 2:
        return None
    
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        return (hour, minute)
    except ValueError:
        return None


def parse_time_to_hours(time_str: str) -> float:
    """
    Парсит время в формате ЧЧ:ММ и конвертирует в часы (десятичное число).

    Например: "8:30" -> 8.5, "1:15" -> 1.25

    Args:
        time_str: строка в формате "ЧЧ:ММ"

    Returns:
        float: количество часов
    """
    parsed = parse_time_to_hours_minutes(time_str)
    if parsed is None:
        raise ValueError(f"Некорректный формат времени: {time_str}")
    
    hour, minute = parsed
    return hour + (minute / 60.0)


@dataclass(slots=True)
class MatchingSettings:
    """
    Типизированные настройки матчинга Random Coffee.

    Содержит все параметры, необходимые для работы алгоритма подбора пар
    и управления жизненным циклом матчей.
    """

    matching_enabled: bool
    match_day: str
    match_msk_time: str  # Формат "ЧЧ:ММ"
    response_timeout_hours: float  # Конвертировано из формата "ЧЧ:ММ" в часы
    reminder_interval_hours: float  # Конвертировано из формата "ЧЧ:ММ" в часы
    repeat_pair_cooldown_weeks: int
    min_jaccard: float


async def load_matching_settings(session: AsyncSession) -> MatchingSettings:
    """
    Загружает и типизирует ключевые настройки матчинга из таблицы settings.

    Читает все необходимые настройки из БД и возвращает их в виде типизированного объекта.
    Использует значения по умолчанию, если настройки отсутствуют.
    Поддерживает миграцию со старого формата (match_msk_hour/minute, часы как числа).

    Args:
        session (AsyncSession): активная сессия БД.

    Returns:
        MatchingSettings: объект с загруженными настройками матчинга.
    """
    # Загрузка времени подбора с поддержкой миграции
    match_time_value = await _get_setting_value(session, "match_msk_time")
    if match_time_value and ":" in match_time_value:
        match_msk_time = match_time_value
    else:
        # Миграция со старого формата
        try:
            hour = await get_setting_int(session, "match_msk_hour")
            minute = await get_setting_int(session, "match_msk_minute")
            match_msk_time = f"{hour:02d}:{minute:02d}"
        except ValueError:
            match_msk_time = "12:00"
    
    # Загрузка таймаута ответа с поддержкой миграции
    timeout_value = await _get_setting_value(session, "response_timeout_hours")
    if timeout_value and ":" in timeout_value:
        response_timeout_hours = parse_time_to_hours(timeout_value)
    else:
        # Миграция со старого формата (часы как число)
        try:
            response_timeout_hours = float(str(timeout_value or "8").strip().replace(",", "."))
        except (TypeError, ValueError):
            response_timeout_hours = 8.0
    
    # Загрузка интервала напоминаний с поддержкой миграции
    interval_value = await _get_setting_value(session, "reminder_interval_hours")
    if interval_value and ":" in interval_value:
        reminder_interval_hours = parse_time_to_hours(interval_value)
    else:
        # Миграция со старого формата (часы как число)
        try:
            reminder_interval_hours = float(str(interval_value or "1").strip().replace(",", "."))
        except (TypeError, ValueError):
            reminder_interval_hours = 1.0
    
    return MatchingSettings(
        matching_enabled=await get_setting_bool(session, "matching_enabled"),
        match_day=await _require_setting(session, "match_day"),
        match_msk_time=match_msk_time,
        response_timeout_hours=response_timeout_hours,
        reminder_interval_hours=reminder_interval_hours,
        repeat_pair_cooldown_weeks=await get_setting_int(
            session,
            "repeat_pair_cooldown_weeks",
        ),
        min_jaccard=await get_setting_float(
            session, "min_jaccard"
        ),
    )

