"""
Утилиты времени и даты.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def now_msk() -> datetime:
    """
    Возвращает текущее время в МСК с информацией о таймзоне.

    Возвращает aware datetime объект (с информацией о часовом поясе),
    установленный на МСК. Используется для консистентной работы со временем в приложении.

    Args:
        None

    Returns:
        datetime: текущее время МСК с информацией о часовом поясе.
    """
    return datetime.now(MOSCOW_TZ)


def ensure_aware_msk(dt: datetime | None) -> datetime | None:
    """
    Приводит datetime объект к aware-МСК формату.

    Если передан None, возвращает None. Если datetime без информации о часовом поясе,
    предполагает, что это МСК и добавляет информацию о часовом поясе. Если datetime aware,
    конвертирует в МСК.

    Args:
        dt (datetime | None): datetime объект для конвертирования или None.

    Returns:
        datetime | None: datetime в МСК с информацией о часовом поясе, или None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MOSCOW_TZ)
    return dt.astimezone(MOSCOW_TZ)