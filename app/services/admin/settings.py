"""
Бизнес-логика управления настройками в панели администратора.
"""

from aiogram.fsm.context import FSMContext
from sqlalchemy.sql import select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Setting

from app.services.const import DAYS_OF_WEEK
from app.handlers.fsm import FSMDataKeys


async def get_current_settings(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """
    Получает текущие настройки из базы данных.
    Возвращает словарь с ключами и значениями.
    """
    async with session_factory() as session:
        result = await session.execute(select(Setting))
        settings = result.scalars().all()

    return {s.key: s.value for s in settings}


async def save_settings(
    session_factory: async_sessionmaker[AsyncSession],
    draft_settings: dict[str, str],
) -> None:
    """
    Сохраняет настройки в базу данных.

    Args:
        session_factory: async_sessionmaker[AsyncSession] - фабрика сессий.
        draft_settings: dict[str, str] - настройки, которые ещё не сохранены в базу данных.
    """
    async with session_factory() as session:
        for key, value in draft_settings.items():
            # Получение настройки из базы данных
            setting = await session.get(Setting, key)

            if setting is None:
                # Нет настройки - пропуск
                continue
            
            setting.value = value

        await session.commit()


def _as_bool(value: str | int | float | None) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "t", "yes", "on"}


def format_settings_text(settings: dict) -> str:
    """
    Форматирует настройки в текстовую строку.

    Примечание: Данный текст будет отображён над меню с настройками панели администратора.
    """
    text = "Настройки для организации встреч.\n\n"
    match_enabled = _as_bool(settings.get("matching_enabled", "true"))
    text += f"🔹 Мэтчинг включён: {'Да' if match_enabled else 'Нет'}\n"
    text += f"🔹 Минимальный Jaccard: {settings.get('min_jaccard', '0.3')}\n"
    text += (
        "🔹 Кулдаун повторной пары (недели): "
        f"{settings.get('repeat_pair_cooldown_weeks', '1')}\n"
    )
    match_day_code = settings.get("match_day", "fri")
    text += (
        "🔹 День подбора: "
        f"{DAYS_OF_WEEK.get(match_day_code, match_day_code)}\n"
    )

    try:
        msk_hour = int(settings.get("match_msk_hour", 12))
        msk_minute = int(settings.get("match_msk_minute", 0))
    except (TypeError, ValueError):
        msk_hour = 12
        msk_minute = 0

    text += f"🔹 Время подбора: {msk_hour:02d}:{msk_minute:02d}\n"
    
    # Форматирование таймаута ответа
    try:
        timeout_hours = float(settings.get("response_timeout_hours", "8"))
        timeout_minutes = int(timeout_hours * 60)
        timeout_h = timeout_minutes // 60
        timeout_m = timeout_minutes % 60
        if timeout_h > 0:
            timeout_display = f"{timeout_h}:{timeout_m:02d}"
        else:
            timeout_display = f"{timeout_m} мин"
    except (TypeError, ValueError):
        timeout_display = settings.get("response_timeout_hours", "8")
    text += f"🔹 Таймаут ответа: {timeout_display}\n"
    
    # Форматирование интервала напоминаний
    try:
        interval_hours = float(settings.get("reminder_interval_hours", "1"))
        interval_minutes = int(interval_hours * 60)
        interval_h = interval_minutes // 60
        interval_m = interval_minutes % 60
        if interval_h > 0:
            interval_display = f"{interval_h}:{interval_m:02d}"
        else:
            interval_display = f"{interval_m} мин"
    except (TypeError, ValueError):
        interval_display = settings.get("reminder_interval_hours", "1")
    text += f"🔹 Интервал напоминаний: {interval_display}\n"

    return text


async def update_draft_setting(
    state: FSMContext,
    key: str,
    value: any
) -> dict:
    """
    Обновляет черновик настроек и возвращает его.

    Args:
        state: FSMContext - контекст состояния.
        key: str - ключ настройки.
        value: any - значение настройки.

    Returns:
        dict - черновик настроек.
    """
    # Получение черновика настроек
    data = await state.get_data()
    draft = (data.get(FSMDataKeys.DRAFT_SETTINGS) or {}).copy()
    # Обновление черновика настроек
    draft[key] = value
    # Сохранение черновика
    await state.update_data(**{FSMDataKeys.DRAFT_SETTINGS: draft})
    
    return draft


def try_to_input_min_jaccard(msg: str) -> float | None:
    """
    Пытается преобразовать введённый текст в числовое значения (типа float).

    Также сразу происходит проверка на вхождение числа в промежуток.
    """
    text = msg.replace(",", ".").strip()

    try:
        value = float(text)
    except ValueError:
        return None

    if 0.1 <= value <= 1.0:
        return value

    return None


def try_to_input_repeat_pair_cooldown_weeks(msg: str) -> int | None:
    """
    Пытается преобразовать введённый текст в числовое значения (типа float).

    Также сразу происходит проверка на вхождение числа в промежуток.
    """
    try:
        value = int(msg.strip())
    except ValueError:
        return None
    
    if 1 <= value <= 12:
        return value

    return None


def try_to_input_match_msk_hour(msg: str) -> int | None:
    """
    Пытается преобразовать введённый текст в числовое значения (типа int).

    Также сразу происходит проверка на вхождение числа в промежуток.
    """

    try:
        value = int(msg.strip())
    except ValueError:
        return None
    
    if 0 <= value <= 23:
        return value

    return None


def try_to_input_match_msk_minute(msg: str) -> int | None:
    """
    Пытается преобразовать введённый текст в числовое значение минут (типа int).

    Также сразу происходит проверка на вхождение числа в промежуток 0-59.
    """
    try:
        value = int(msg.strip())
    except ValueError:
        return None
    
    if 0 <= value <= 59:
        return value

    return None
