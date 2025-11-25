"""
Утилиты времени и даты.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    """
    Возвращает текущее время в UTC с информацией о таймзоне.

    Возвращает aware datetime объект (с информацией о часовом поясе),
    установленный на UTC. Используется для консистентной работы со временем в приложении.

    Args:
        None

    Returns:
        datetime: текущее время UTC с информацией о часовом поясе.
    """
    return datetime.now(timezone.utc)


def ensure_aware_utc(dt: datetime | None) -> datetime | None:
    """
    Приводит datetime объект к aware-UTC формату.

    Если передан None, возвращает None. Если datetime наивный (без информации о часовом поясе),
    предполагает, что это UTC и добавляет информацию о часовом поясе. Если datetime aware,
    конвертирует в UTC.

    Args:
        dt (datetime | None): datetime объект для конвертирования или None.

    Returns:
        datetime | None: datetime в UTC с информацией о часовом поясе, или None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Считаем, что «наивное» время — это UTC в нашей системе.
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)