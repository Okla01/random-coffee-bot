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


def format_time_readable(time_str: str) -> str:
    """
    Форматирует время в формате ЧЧ:ММ в читаемый формат.
    
    Примеры:
    - "00:01" -> "1 минута"
    - "00:02" -> "2 минуты"
    - "00:05" -> "5 минут"
    - "1:00" -> "1 час"
    - "2:00" -> "2 часа"
    - "5:00" -> "5 часов"
    - "1:30" -> "1 час 30 минут"
    - "2:15" -> "2 часа 15 минут"
    
    Args:
        time_str: строка в формате "ЧЧ:ММ"
    
    Returns:
        str: отформатированная строка с правильными склонениями
    """
    if ":" not in time_str:
        return time_str
    
    try:
        parts = time_str.split(":")
        hours = int(parts[0])
        minutes = int(parts[1])
    except (ValueError, IndexError):
        return time_str
    
    # Функция для склонения минут
    def format_minutes(m: int) -> str:
        if m == 0:
            return ""
        if m == 1:
            return "1 минута"
        elif 2 <= m <= 4:
            return f"{m} минуты"
        elif 5 <= m <= 20:
            return f"{m} минут"
        elif m % 10 == 1:
            return f"{m} минута"
        elif m % 10 in (2, 3, 4):
            return f"{m} минуты"
        else:
            return f"{m} минут"
    
    # Функция для склонения часов
    def format_hours(h: int) -> str:
        if h == 0:
            return ""
        if h == 1:
            return "1 час"
        elif 2 <= h <= 4:
            return f"{h} часа"
        elif 5 <= h <= 20:
            return f"{h} часов"
        elif h % 10 == 1:
            return f"{h} час"
        elif h % 10 in (2, 3, 4):
            return f"{h} часа"
        else:
            return f"{h} часов"
    
    # Формируем результат
    if hours == 0 and minutes == 0:
        return "0 минут"
    elif hours == 0:
        return format_minutes(minutes)
    elif minutes == 0:
        return format_hours(hours)
    else:
        return f"{format_hours(hours)} {format_minutes(minutes)}"


def format_settings_text(settings: dict) -> str:
    """
    Форматирует настройки в текстовую строку.

    Примечание: Данный текст будет отображён над меню с настройками панели администратора.
    Порядок соответствует порядку кнопок в клавиатуре.
    """
    text = "Настройки для организации встреч.\nВыберете настройку, которую хотите изменить.\n\n"
    
    # 1. Мэтчинг включён
    match_enabled = _as_bool(settings.get("matching_enabled", "true"))
    text += f"🔹 Мэтчинг включён: {'Да' if match_enabled else 'Нет'}\n"
    
    # 2. День подбора
    match_day_code = settings.get("match_day", "fri")
    text += (
        "🔹 День подбора: "
        f"{DAYS_OF_WEEK.get(match_day_code, match_day_code)}\n"
    )
    
    # 3. Время подбора
    match_time = settings.get("match_msk_time", "12:00")
    if ":" not in match_time:
        # Миграция со старого формата
        try:
            msk_hour = int(settings.get("match_msk_hour", 12))
            msk_minute = int(settings.get("match_msk_minute", 0))
            match_time = f"{msk_hour:02d}:{msk_minute:02d}"
        except (TypeError, ValueError):
            match_time = "12:00"
    # Время подбора оставляем в формате ЧЧ:ММ (это время суток, не интервал)
    text += f"🔹 Время подбора: {match_time}\n"
    
    # 4. Минимальный Jaccard
    text += f"🔹 Минимальный Jaccard: {settings.get('min_jaccard', '0.3')}\n"
    
    # 5. Кулдаун повторов
    text += (
        "🔹 Кулдаун повторов (недели): "
        f"{settings.get('repeat_pair_cooldown_weeks', '1')}\n"
    )
    
    # 6. Таймаут ответа
    timeout_value = settings.get("response_timeout_time") or settings.get("response_timeout_hours", "8:00")
    if ":" in timeout_value:
        timeout_display = format_time_readable(timeout_value)
    else:
        # Миграция со старого формата (часы как число)
        try:
            timeout_hours = float(timeout_value)
            timeout_minutes = int(timeout_hours * 60)
            timeout_h = timeout_minutes // 60
            timeout_m = timeout_minutes % 60
            timeout_display = format_time_readable(f"{timeout_h}:{timeout_m:02d}")
        except (TypeError, ValueError):
            timeout_display = timeout_value
    text += f"🔹 Таймаут ответа: {timeout_display}\n"
    
    # 7. Интервал напоминаний
    interval_value = settings.get("reminder_interval_time") or settings.get("reminder_interval_hours", "1:00")
    if ":" in interval_value:
        interval_display = format_time_readable(interval_value)
    else:
        # Миграция со старого формата (часы как число)
        try:
            interval_hours = float(interval_value)
            interval_minutes = int(interval_hours * 60)
            interval_h = interval_minutes // 60
            interval_m = interval_minutes % 60
            interval_display = format_time_readable(f"{interval_h}:{interval_m:02d}")
        except (TypeError, ValueError):
            interval_display = interval_value
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


def try_to_input_time(msg: str) -> str | None:
    """
    Пытается преобразовать введённый текст во время в формате ЧЧ:ММ.

    Проверяет корректность формата и значений (час 0-23, минуты 0-59).
    Возвращает строку в формате "ЧЧ:ММ" или None при некорректном вводе.
    """
    text = msg.strip()
    
    # Проверка формата ЧЧ:ММ
    if ":" not in text:
        return None
    
    parts = text.split(":")
    if len(parts) != 2:
        return None
    
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    
    # Проверка диапазонов
    if not (0 <= hour <= 23):
        return None
    if not (0 <= minute <= 59):
        return None
    
    return f"{hour:02d}:{minute:02d}"


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


def parse_time_to_hours(time_str: str) -> float | None:
    """
    Парсит время в формате ЧЧ:ММ и конвертирует в часы (десятичное число).

    Например: "8:30" -> 8.5, "1:15" -> 1.25

    Args:
        time_str: строка в формате "ЧЧ:ММ"

    Returns:
        float | None: количество часов или None при ошибке
    """
    parsed = parse_time_to_hours_minutes(time_str)
    if parsed is None:
        return None
    
    hour, minute = parsed
    return hour + (minute / 60.0)


def toggle_matching_enabled(current_value: str | bool) -> str:
    """
    Переключает значение matching_enabled (true/false).

    Args:
        current_value: текущее значение (строка или bool)

    Returns:
        str: "true" или "false"
    """
    current_bool = _as_bool(current_value)
    return "false" if current_bool else "true"


def try_to_input_time_as_hours(msg: str) -> str | None:
    """
    Пытается преобразовать введённый текст во время в формате ЧЧ:ММ
    и возвращает его как есть для сохранения.

    Используется для response_timeout_time и reminder_interval_time.
    """
    return try_to_input_time(msg)
