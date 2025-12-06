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
    result = await session.execute(select(Setting.value).where(Setting.key == key))
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
        raise ValueError(
            f"Настройка '{key}' содержит некорректное целое число"
        ) from exc


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


def parse_time_to_minutes(time_str: str) -> float:
    """
    Парсит время в формате ЧЧ:ММ и конвертирует в минуты (десятичное число).

    Например: "0:30" -> 30.0, "1:15" -> 75.0, "2:05" -> 125.0

    Args:
        time_str: строка в формате "ЧЧ:ММ"

    Returns:
        float: количество минут
    """
    parsed = parse_time_to_hours_minutes(time_str)
    if parsed is None:
        raise ValueError(f"Некорректный формат времени: {time_str}")

    hour, minute = parsed
    return (hour * 60.0) + minute


def calculate_optimal_scheduler_interval(
    reminder_interval_time: str,
    response_timeout_time: str,
) -> tuple[float, str]:
    """
    Вычисляет оптимальный интервал планировщика на основе настроек напоминаний и таймаутов.

    Интервал планировщика должен быть достаточно маленьким, чтобы гарантировать
    своевременную отправку напоминаний и обработку таймаутов.

    Логика:
    - Берется половина интервала напоминаний, чтобы напоминания отправлялись точно
    - Также учитывается таймаут (берется 1/10 часть для точности обработки)
    - Выбирается минимальное значение, но не меньше 1 минуты

    Args:
        reminder_interval_time: интервал напоминаний в формате "ЧЧ:ММ"
        response_timeout_time: таймаут ответа в формате "ЧЧ:ММ"

    Returns:
        tuple[float, str]: (интервал, единица измерения) где единица - "minutes" или "seconds"
    """
    reminder_minutes = parse_time_to_minutes(reminder_interval_time)
    timeout_minutes = parse_time_to_minutes(response_timeout_time)

    # Половина интервала напоминаний (чтобы напоминания отправлялись точно)
    reminder_half = reminder_minutes / 2.0

    # 1/10 таймаута (для точной обработки таймаутов)
    timeout_tenth = timeout_minutes / 10.0

    # Выбираем минимальное значение
    optimal_interval_minutes = min(reminder_half, timeout_tenth)

    # Если интервал меньше 1 минуты, используем секунды для большей точности
    if optimal_interval_minutes < 1.0:
        optimal_interval_seconds = optimal_interval_minutes * 60.0
        # Округляем до целого числа секунд, минимум 30 секунд
        return (max(30.0, round(optimal_interval_seconds)), "seconds")
    else:
        # Округляем до 1 знака после запятой, минимум 1 минута
        return (max(1.0, round(optimal_interval_minutes, 1)), "minutes")


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
    response_timeout_time: str  # Формат "ЧЧ:ММ"
    reminder_interval_time: str  # Формат "ЧЧ:ММ"
    repeat_pair_cooldown_weeks: int
    min_jaccard: float
    feedback_day: str
    feedback_msk_time: str  # Формат "ЧЧ:ММ"


async def load_matching_settings(session: AsyncSession) -> MatchingSettings:
    """
    Загружает и типизирует ключевые настройки матчинга из таблицы settings.

    Читает все необходимые настройки из БД и возвращает их в виде типизированного объекта.
    Использует значения по умолчанию, если настройки отсутствуют.
    Поддерживает миграцию со старого формата (match_msk_hour/minute, response_timeout_hours, reminder_interval_hours).

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
    timeout_value = await _get_setting_value(session, "response_timeout_time")
    if not timeout_value:
        # Пробуем старое имя для миграции
        timeout_value = await _get_setting_value(session, "response_timeout_hours")

    if timeout_value and ":" in timeout_value:
        response_timeout_time = timeout_value
    else:
        # Миграция со старого формата (часы как число)
        try:
            hours_float = float(str(timeout_value or "8").strip().replace(",", "."))
            hours = int(hours_float)
            minutes = int((hours_float - hours) * 60)
            response_timeout_time = f"{hours:02d}:{minutes:02d}"
        except (TypeError, ValueError):
            response_timeout_time = "8:00"

    # Загрузка интервала напоминаний с поддержкой миграции
    interval_value = await _get_setting_value(session, "reminder_interval_time")
    if not interval_value:
        # Пробуем старое имя для миграции
        interval_value = await _get_setting_value(session, "reminder_interval_hours")

    if interval_value and ":" in interval_value:
        reminder_interval_time = interval_value
    else:
        # Миграция со старого формата (часы как число)
        try:
            hours_float = float(str(interval_value or "1").strip().replace(",", "."))
            hours = int(hours_float)
            minutes = int((hours_float - hours) * 60)
            reminder_interval_time = f"{hours:02d}:{minutes:02d}"
        except (TypeError, ValueError):
            reminder_interval_time = "1:00"

    # Загрузка настроек обратной связи
    feedback_day = await _get_setting_value(session, "feedback_day") or "sun"
    feedback_msk_time = await _get_setting_value(session, "feedback_msk_time") or "18:00"

    return MatchingSettings(
        matching_enabled=await get_setting_bool(session, "matching_enabled"),
        match_day=await _require_setting(session, "match_day"),
        match_msk_time=match_msk_time,
        response_timeout_time=response_timeout_time,
        reminder_interval_time=reminder_interval_time,
        repeat_pair_cooldown_weeks=await get_setting_int(
            session,
            "repeat_pair_cooldown_weeks",
        ),
        min_jaccard=await get_setting_float(session, "min_jaccard"),
        feedback_day=feedback_day,
        feedback_msk_time=feedback_msk_time,
    )
