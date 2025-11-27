from mailbox import Message
from sqlalchemy.sql import select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Setting

from app.services.const import DAYS_OF_WEEK


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


def format_settings_text(settings: dict) -> str:
    """
    Форматирует настройки в текстовую строку.

    Данный текст будет отображён над меню с настройками панели администратора.
    """
    text = "Настройки для организации встреч.\n\n"
    text += f"🔹 Минимальный Jaccard: {settings['min_jaccard']}\n"
    text += f"🔹 Периодичность встреч (недели): {settings['cooldown_weeks']}\n"
    text += (
        "🔹 День недели для встреч: "
        f"{DAYS_OF_WEEK.get(settings['match_day'], settings['match_day'])}\n"
    )
    text += f"🔹 Час совпадения (UTC): {settings['match_utc_hour']}\n"
    
    return text


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