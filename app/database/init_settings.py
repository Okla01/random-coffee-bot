"""
Инициализация дефолтных настроек в таблице Settings.

Содержит функцию для создания дефолтных настроек при первом запуске приложения.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Setting


# Дефолтные настройки, которые будут созданы при инициализации
DEFAULT_SETTINGS: dict[str, str] = {
    "min_jaccard": "0.3",
    "cooldown_weeks": "1",
    "match_day": "fri",
    "match_utc_hour": "12",
}


async def init_default_settings(session: AsyncSession) -> None:
    """
    Инициализирует дефолтные настройки в таблице Settings.

    Создаёт записи для всех настроек из DEFAULT_SETTINGS, если они ещё не существуют.
    Не перезаписывает существующие настройки.

    Args:
        session (AsyncSession): сессия БД.

    Returns:
        None: ничего не возвращает.
    """
    for key, default_value in DEFAULT_SETTINGS.items():
        # Проверка, существует ли уже настройка
        existing = (
            await session.execute(select(Setting).where(Setting.key == key))
        ).scalar_one_or_none()

        if not existing:
            # Создаём новую настройку с дефолтным значением
            setting = Setting(key=key, value=default_value)
            session.add(setting)

    await session.flush()

