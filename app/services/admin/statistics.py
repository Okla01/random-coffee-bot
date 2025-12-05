"""
Бизнес-логика получения статистики для административной панели.

Содержит функции для получения различных метрик системы.
"""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Match, User
from app.database.utils import now_msk
from app.services.const import USER_STATUS_ACTIVE, USER_STATUS_BLOCKED


# Названия периодов для отображения
PERIOD_LABELS = {
    "7_days": "7 дней",
    "30_days": "30 дней",
    "6_months": "6 месяцев",
    "all_time": "всё время",
}


async def get_active_users_count(session: AsyncSession) -> int:
    """
    Получает количество активных пользователей.

    Args:
        session (AsyncSession): сессия БД.

    Returns:
        int: количество активных пользователей.
    """
    result = await session.scalar(
        select(func.count(User.id)).where(User.status == USER_STATUS_ACTIVE)
    )
    return result or 0


async def get_blocked_users_count(session: AsyncSession) -> int:
    """
    Получает количество заблокированных пользователей.

    Args:
        session (AsyncSession): сессия БД.

    Returns:
        int: количество заблокированных пользователей.
    """
    result = await session.scalar(
        select(func.count(User.id)).where(User.status == USER_STATUS_BLOCKED)
    )
    return result or 0


async def get_new_users_count(session: AsyncSession, period: str) -> int:
    """
    Получает количество новых пользователей за период.

    Новым считается пользователь, который зарегистрировался в течение
    указанного периода и имеет статус "Активный".

    Args:
        session (AsyncSession): сессия БД.
        period (str): код периода (7_days, 30_days, 6_months, all_time).

    Returns:
        int: количество новых пользователей.
    """
    period_start = _get_period_start(period)
    query = select(func.count(User.id)).where(User.status == USER_STATUS_ACTIVE)
    if period_start:
        query = query.where(User.registered_at >= period_start)
    result = await session.scalar(query)
    return result or 0


def _get_period_start(period: str):
    """
    Возвращает дату начала периода.

    Args:
        period (str): код периода (7_days, 30_days, 6_months, all_time).

    Returns:
        datetime | None: дата начала периода или None для all_time.
    """
    now = now_msk()
    if period == "7_days":
        return now - timedelta(days=7)
    elif period == "30_days":
        return now - timedelta(days=30)
    elif period == "6_months":
        return now - timedelta(days=180)
    return None  # all_time


async def get_total_matches_count(session: AsyncSession, period: str) -> int:
    """
    Получает общее количество мэтчей за период.

    Args:
        session (AsyncSession): сессия БД.
        period (str): код периода.

    Returns:
        int: количество мэтчей.
    """
    period_start = _get_period_start(period)
    query = select(func.count(Match.id))
    if period_start:
        query = query.where(Match.created_at >= period_start)
    result = await session.scalar(query)
    return result or 0


async def get_successful_matches_count(session: AsyncSession, period: str) -> int:
    """
    Получает количество успешных мэтчей за период.

    Успешным считается мэтч, у которого заполнено поле meeting_start_at.
    Период считается по дате начала встречи (meeting_start_at), а не по дате создания мэтча.

    Args:
        session (AsyncSession): сессия БД.
        period (str): код периода.

    Returns:
        int: количество успешных мэтчей.
    """
    period_start = _get_period_start(period)
    query = select(func.count(Match.id)).where(Match.meeting_start_at.isnot(None))
    if period_start:
        query = query.where(Match.meeting_start_at >= period_start)
    result = await session.scalar(query)
    return result or 0


async def get_average_jaccard_score(session: AsyncSession, period: str) -> float | None:
    """
    Получает средний Jaccard-коэффициент из матчей за период.

    Args:
        session (AsyncSession): сессия БД.
        period (str): код периода.

    Returns:
        float | None: средний Jaccard-коэффициент или None, если матчей нет.
    """
    period_start = _get_period_start(period)
    query = select(func.avg(Match.jaccard_score))
    if period_start:
        query = query.where(Match.created_at >= period_start)
    result = await session.scalar(query)
    return float(result) if result is not None else None


async def get_general_statistics(session: AsyncSession) -> dict[str, int]:
    """
    Получает общую статистику (не зависящую от периода).

    Args:
        session (AsyncSession): сессия БД.

    Returns:
        dict[str, int]: словарь с метриками:
            - active_users_count: количество активных пользователей
            - blocked_users_count: количество заблокированных пользователей
    """
    return {
        "active_users_count": await get_active_users_count(session),
        "blocked_users_count": await get_blocked_users_count(session),
    }


async def get_period_statistics(
    session: AsyncSession, period: str
) -> dict[str, int | float | None]:
    """
    Получает статистику за указанный период.

    Args:
        session (AsyncSession): сессия БД.
        period (str): код периода (7_days, 30_days, 6_months, all_time).

    Returns:
        dict[str, int | float | None]: словарь с метриками:
            - new_users: новых пользователей
            - total_matches: всего мэтчей
            - successful_matches: успешных мэтчей
            - average_jaccard_score: средний Jaccard-коэффициент
    """
    return {
        "new_users": await get_new_users_count(session, period),
        "total_matches": await get_total_matches_count(session, period),
        "successful_matches": await get_successful_matches_count(session, period),
        "average_jaccard_score": await get_average_jaccard_score(session, period),
    }


async def get_all_statistics(
    session: AsyncSession, period: str = "7_days"
) -> dict[str, int | float | None | str]:
    """
    Получает все метрики статистики.

    Args:
        session (AsyncSession): сессия БД.
        period (str): код периода.

    Returns:
        dict: словарь со всеми метриками.
    """
    general = await get_general_statistics(session)
    period_stats = await get_period_statistics(session, period)
    return {
        **general,
        **period_stats,
        "period": period,
    }


def format_statistics_text(stats: dict[str, int | float | None | str]) -> str:
    """
    Форматирует статистику для отображения в сообщении.

    Args:
        stats (dict): словарь с метриками.

    Returns:
        str: отформатированный текст статистики.
    """
    period = stats.get("period", "7_days")
    period_label = PERIOD_LABELS.get(period, period)

    lines = ["📊 <b>Статистика</b>\n"]

    # Общая статистика
    lines.append("<b>Общая статистика</b>")
    active_count = stats.get("active_users_count", 0)
    lines.append(f"👥 Активных пользователей: <b>{active_count}</b>")
    blocked_count = stats.get("blocked_users_count", 0)
    lines.append(f"🔒 Заблокированных пользователей: <b>{blocked_count}</b>")

    lines.append("")  # Пустая строка

    # Статистика за период
    lines.append(f"<b>Статистика за {period_label}</b>")

    new_users = stats.get("new_users", 0)
    lines.append(f"🆕 Новых пользователей: <b>{new_users}</b>")

    total_matches = stats.get("total_matches", 0)
    lines.append(f"📃 Всего мэтчей: <b>{total_matches}</b>")

    successful_matches = stats.get("successful_matches", 0)
    lines.append(f"🎇 Успешных мэтчей: <b>{successful_matches}</b>")

    avg_jaccard = stats.get("average_jaccard_score")
    if avg_jaccard is not None:
        lines.append(f"📈 Средний Jaccard-коэффициент: <b>{avg_jaccard:.3f}</b>")
    else:
        lines.append("📈 Средний Jaccard-коэффициент: <b>—</b>")

    return "\n".join(lines)
