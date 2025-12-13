"""
Бизнес-логика работы с интересами пользователя.
"""

from __future__ import annotations

from typing import Iterable, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.const import (
    INTERESTS_PAGE_SIZE,
    MAX_INTERESTS_COUNT,
    MIN_INTERESTS_COUNT,
    UNIVERSAL_INTERESTS,
)
from app.services.core.config import Settings
from app.services.profile.types import FieldResult
from app.services.profile.utils import is_profile_complete
from app.database.models import User

_LOWER_TO_CANONICAL = {interest.lower(): interest for interest in UNIVERSAL_INTERESTS}


def normalize_selected_interests(raw: Iterable[str] | None) -> list[str]:
    """
    Оставляет только допустимые интересы, упорядочивая их как в справочном списке.
    """
    if not raw:
        return []
    seen = set()
    selected: list[str] = []
    for interest in raw:
        canonical = _LOWER_TO_CANONICAL.get(interest.strip().lower())
        if not canonical:
            continue
        if canonical not in seen:
            seen.add(canonical)
            selected.append(canonical)
    # упорядочиваем по справочному списку для стабильности
    order = {name: idx for idx, name in enumerate(UNIVERSAL_INTERESTS)}
    selected.sort(key=lambda name: order.get(name, len(order)))
    return selected


def toggle_interest(
    selected: list[str], interest: str, max_allowed: int = MAX_INTERESTS_COUNT
) -> Tuple[list[str], str | None]:
    """
    Переключает выбор интереса, контролируя верхний предел.
    """
    if interest not in UNIVERSAL_INTERESTS:
        return selected, "Этот интерес недоступен для выбора."

    if interest in selected:
        updated = [item for item in selected if item != interest]
        return updated, None

    if len(selected) >= max_allowed:
        return selected, f"Можно выбрать не более {max_allowed} интересов."

    updated = normalize_selected_interests([*selected, interest])
    return updated, None


def can_save_interests(selected: list[str]) -> bool:
    """
    Проверяет, достаточно ли выбранных интересов для сохранения.
    """
    return MIN_INTERESTS_COUNT <= len(selected) <= MAX_INTERESTS_COUNT


async def process_interests_field(
    session: AsyncSession,
    user: User,
    selected: list[str],
    settings: Settings,
    editing_field: str | None,
) -> FieldResult:
    """
    Сохраняет выбранные интересы пользователя (выбор из универсального справочника).
    """
    _ = settings  # для совместимости сигнатуры
    normalized = normalize_selected_interests(selected)

    if not can_save_interests(normalized):
        await session.commit()
        return FieldResult(
            result_type="validation_error",
            error_message=(
                f"⚠️ Выбери от {MIN_INTERESTS_COUNT} до {MAX_INTERESTS_COUNT} интересов, "
                f"сейчас выбрано {len(normalized)}."
            ),
        )

    user.interests_json = {"interests": normalized}
    is_editing = editing_field == "interests" or is_profile_complete(user)
    user.stage = "profile_review"
    await session.commit()

    if is_editing:
        return FieldResult(
            result_type="field_updated_review",
            next_stage="profile_review",
            is_editing=True,
        )

    return FieldResult(
        result_type="field_updated_continue",
        next_stage="profile_review",
        is_editing=False,
    )


def clamp_page(page: int, total_items: int, page_size: int = INTERESTS_PAGE_SIZE) -> int:
    """
    Ограничивает номер страницы валидным диапазоном.
    """
    if page_size <= 0:
        return 1
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    return max(1, min(page, total_pages))
