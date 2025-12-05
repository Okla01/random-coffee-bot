"""
Логика генерации графиков статистики.

Создаёт два графика:
- Количество мэтчей и успешных мэтчей за 6 месяцев (по неделям)
- Средний Jaccard-коэффициент за 6 месяцев (по неделям)
"""

from datetime import timedelta
from io import BytesIO

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Match
from app.database.utils import now_msk


# Настройки графиков
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["figure.facecolor"] = "white"
plt.rcParams["axes.facecolor"] = "white"
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.3


async def _get_weekly_stats(
    session: AsyncSession,
    weeks: int = 26,
) -> list[dict]:
    """
    Получает статистику по неделям за указанный период.

    Args:
        session (AsyncSession): сессия БД.
        weeks (int): количество недель для анализа.

    Returns:
        list[dict]: список словарей с данными по каждой неделе.
    """
    now = now_msk()
    stats = []

    for week_offset in range(weeks - 1, -1, -1):
        week_end = now - timedelta(weeks=week_offset)
        week_start = week_end - timedelta(days=7)

        # Всего мэтчей за неделю (по created_at)
        total_matches = await session.scalar(
            select(func.count(Match.id)).where(
                and_(
                    Match.created_at >= week_start,
                    Match.created_at < week_end,
                )
            )
        )
        total_matches = total_matches or 0

        # Успешных мэтчей за неделю (по meeting_start_at)
        successful_matches = await session.scalar(
            select(func.count(Match.id)).where(
                and_(
                    Match.meeting_start_at >= week_start,
                    Match.meeting_start_at < week_end,
                )
            )
        )
        successful_matches = successful_matches or 0

        # Средний Jaccard за неделю (для мэтчей созданных в эту неделю)
        avg_jaccard = await session.scalar(
            select(func.avg(Match.jaccard_score)).where(
                and_(
                    Match.created_at >= week_start,
                    Match.created_at < week_end,
                )
            )
        )
        avg_jaccard = float(avg_jaccard) if avg_jaccard is not None else None

        stats.append(
            {
                "week_end": week_end,
                "total_matches": total_matches,
                "successful_matches": successful_matches,
                "avg_jaccard": avg_jaccard,
            }
        )

    return stats


def _generate_matches_graph(stats: list[dict]) -> BytesIO:
    """
    Генерирует график количества мэтчей и успешных мэтчей.

    Args:
        stats (list[dict]): данные статистики по неделям.

    Returns:
        BytesIO: изображение графика в формате PNG.
    """
    dates = [s["week_end"] for s in stats]
    total_matches = [s["total_matches"] for s in stats]
    successful_matches = [s["successful_matches"] for s in stats]

    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(
        dates, total_matches, "b-o", label="Всего мэтчей", linewidth=2, markersize=4
    )
    ax.plot(
        dates,
        successful_matches,
        "g-o",
        label="Успешных мэтчей",
        linewidth=2,
        markersize=4,
    )

    ax.fill_between(dates, total_matches, alpha=0.2, color="blue")
    ax.fill_between(dates, successful_matches, alpha=0.2, color="green")

    ax.set_xlabel("Дата", fontsize=12)
    ax.set_ylabel("Количество мэтчей", fontsize=12)
    ax.set_title(
        "Мэтчи за последние 6 месяцев (по неделям)", fontsize=14, fontweight="bold"
    )

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.xticks(rotation=45)

    ax.legend(loc="upper left")
    ax.set_ylim(bottom=0)

    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)

    return buf


def _generate_jaccard_graph(stats: list[dict]) -> BytesIO:
    """
    Генерирует график среднего Jaccard-коэффициента.

    Args:
        stats (list[dict]): данные статистики по неделям.

    Returns:
        BytesIO: изображение графика в формате PNG.
    """
    # Фильтруем недели без данных
    dates = []
    jaccard_values = []

    for s in stats:
        if s["avg_jaccard"] is not None:
            dates.append(s["week_end"])
            jaccard_values.append(s["avg_jaccard"])

    fig, ax = plt.subplots(figsize=(12, 6))

    if dates and jaccard_values:
        ax.plot(
            dates,
            jaccard_values,
            "r-o",
            label="Средний Jaccard",
            linewidth=2,
            markersize=4,
        )
        ax.fill_between(dates, jaccard_values, alpha=0.2, color="red")

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        plt.xticks(rotation=45)

        ax.set_ylim(0, 1)
    else:
        ax.text(
            0.5,
            0.5,
            "Нет данных за период",
            ha="center",
            va="center",
            fontsize=14,
            transform=ax.transAxes,
        )

    ax.set_xlabel("Дата", fontsize=12)
    ax.set_ylabel("Jaccard-коэффициент", fontsize=12)
    ax.set_title(
        "Средний Jaccard-коэффициент за последние 6 месяцев (по неделям)",
        fontsize=14,
        fontweight="bold",
    )

    ax.legend(loc="upper left")

    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)

    return buf


async def generate_statistics_graphs(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[BytesIO, BytesIO]:
    """
    Генерирует графики статистики за 6 месяцев.

    Args:
        session_factory (async_sessionmaker[AsyncSession]): фабрика сессий БД.

    Returns:
        tuple[BytesIO, BytesIO]: (график мэтчей, график Jaccard).
    """
    async with session_factory() as session:
        stats = await _get_weekly_stats(session, weeks=26)

    matches_graph = _generate_matches_graph(stats)
    jaccard_graph = _generate_jaccard_graph(stats)

    return matches_graph, jaccard_graph
