"""
Запуск раунда матчинга Random Coffee.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.types import InputMediaPhoto
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Match, User
from app.database.utils import now_msk
from app.keyboards.kb_matching import kb_match_invitation
from app.services.const import USER_STATUS_ACTIVE
from app.services.matching.constants import MATCH_ACTIVE_STATUSES
from app.services.matching.settings import MatchingSettings, load_matching_settings
from app.services.matching.utils import (
    compute_jaccard,
    extract_interests_list,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PairingEdge:
    """
    Представление потенциальной пары пользователей для матчинга.

    Attributes:
        user_a (User): первый пользователь пары.
        user_b (User): второй пользователь пары.
        jaccard (float): коэффициент Жаккара (схожесть интересов) в диапазоне [0.0, 1.0].
        priority (float): приоритет пары для жадного алгоритма (чем выше, тем лучше).
    """

    user_a: User
    user_b: User
    jaccard: float
    priority: float


async def run_matching_round(session: AsyncSession, bot: Bot) -> None:
    """
    Главная процедура запуска раунда матчинга.

    Загружает настройки, фильтрует кандидатов, строит пары, сохраняет матчи
    и отправляет уведомления участникам.

    Args:
        session (AsyncSession): активная сессия БД.
        bot (Bot): экземпляр бота для отправки уведомлений.

    Returns:
        None: ничего не возвращает.
    """
    settings = await load_matching_settings(session)
    if not settings.matching_enabled:
        logger.info("Matching round skipped: disabled in settings.")
        return

    now = now_msk()
    candidates = await _load_candidates(session, settings, now)
    if len(candidates) < 2:
        await _notify_no_pairs(bot, candidates)
        return

    recent_pairs = await _load_recent_pairs(session, settings, now)
    edges = _build_pairing_edges(candidates, recent_pairs, settings, now)

    if not edges:
        await _notify_no_pairs(bot, candidates)
        return

    selected_pairs = _select_pairs(edges)
    if not selected_pairs:
        await _notify_no_pairs(bot, candidates)
        return

    created_matches = await _persist_matches(session, selected_pairs, now)
    await session.flush()  # Сохраняем match в БД для получения ID
    await _notify_new_matches(session, bot, created_matches)
    await session.commit()  # Коммитим после отправки всех уведомлений и сохранения message_id

    matched_user_ids = {
        user_id
        for pair in selected_pairs
        for user_id in (pair.user_a.id, pair.user_b.id)
    }
    unmatched_users = [user for user in candidates if user.id not in matched_user_ids]
    if unmatched_users:
        await _notify_no_pairs(bot, unmatched_users)


async def _load_candidates(
    session: AsyncSession,
    settings: MatchingSettings,
    now,
) -> list[User]:
    """
    Возвращает список пользователей, готовых к участию в новом раунде.

    Фильтрует пользователей по статусу, стадии профиля, отсутствию активных матчей
    и соблюдению кулдауна last_pairing_at.

    Args:
        session (AsyncSession): активная сессия БД.
        settings (MatchingSettings): настройки матчинга.
        now: текущее время в МСК.

    Returns:
        list[User]: список пользователей-кандидатов для матчинга.
    """
    active_ids = await _load_users_with_active_matches(session)

    stmt: Select[tuple[User]] = select(User).where(
        User.status == USER_STATUS_ACTIVE,
        User.stage == "profile_filled",
    )
    if active_ids:
        stmt = stmt.where(~User.id.in_(active_ids))

    result = await session.execute(stmt)
    users = list(result.scalars().all())

    cooldown_weeks = max(settings.repeat_pair_cooldown_weeks, 1)
    cooldown_delta = timedelta(weeks=cooldown_weeks)
    eligible: list[User] = []
    for user in users:
        if not user.telegram_id:
            continue
        if user.last_pairing_at and now - user.last_pairing_at < cooldown_delta:
            continue
        eligible.append(user)
    return eligible


async def _load_users_with_active_matches(session: AsyncSession) -> set[int]:
    """
    Возвращает множество user_id, у которых есть активные матчи.

    Args:
        session (AsyncSession): активная сессия БД.

    Returns:
        set[int]: множество ID пользователей с активными матчами.
    """
    active_ids: set[int] = set()
    stmt = select(Match.user_a_id, Match.user_b_id).where(
        Match.status.in_(MATCH_ACTIVE_STATUSES)
    )
    result = await session.execute(stmt)
    for user_a_id, user_b_id in result.all():
        active_ids.add(user_a_id)
        active_ids.add(user_b_id)
    active_ids.discard(None)
    return active_ids


async def _load_recent_pairs(
    session: AsyncSession,
    settings: MatchingSettings,
    now,
) -> set[frozenset[int]]:
    """
    Возвращает пары пользователей, которые встречались в недавних матчах.

    Используется для исключения повторных пар в рамках cooldown периода.

    Args:
        session (AsyncSession): активная сессия БД.
        settings (MatchingSettings): настройки матчинга (repeat_pair_cooldown_weeks).
        now: текущее время в МСК.

    Returns:
        set[frozenset[int]]: множество пар (frozenset из двух user_id), созданных
            в течение repeat_pair_cooldown_weeks.
    """
    if settings.repeat_pair_cooldown_weeks <= 0:
        return set()

    threshold = now - timedelta(weeks=settings.repeat_pair_cooldown_weeks)
    stmt = select(Match.user_a_id, Match.user_b_id).where(Match.created_at >= threshold)
    result = await session.execute(stmt)
    return {frozenset((row[0], row[1])) for row in result.all()}


def _build_pairing_edges(
    candidates: list[User],
    recent_pairs: set[frozenset[int]],
    settings: MatchingSettings,
    now,
) -> list[PairingEdge]:
    """
    Строит список рёбер (потенциальных пар) с весами.

    Вычисляет коэффициент Жаккара для каждой пары кандидатов, фильтрует по min_jaccard
    и исключает недавние пары. Сортирует рёбра по приоритету (убывание).

    Args:
        candidates (list[User]): список кандидатов для матчинга.
        recent_pairs (set[frozenset[int]]): множество недавних пар для исключения.
        settings (MatchingSettings): настройки матчинга (min_jaccard).
        now: текущее время в МСК.

    Returns:
        list[PairingEdge]: отсортированный по приоритету список потенциальных пар.
    """
    edges: list[PairingEdge] = []
    precomputed_interests = {
        user.id: extract_interests_list(user.interests_json) for user in candidates
    }

    for idx, user_a in enumerate(candidates):
        interests_a = precomputed_interests.get(user_a.id) or []
        for user_b in candidates[idx + 1 :]:
            pair_key = frozenset((user_a.id, user_b.id))
            if pair_key in recent_pairs:
                continue

            interests_b = precomputed_interests.get(user_b.id) or []
            jaccard = compute_jaccard(interests_a, interests_b)
            if jaccard < settings.min_jaccard:
                continue

            priority = _calc_priority(user_a, user_b, now, jaccard)
            edges.append(
                PairingEdge(
                    user_a=user_a,
                    user_b=user_b,
                    jaccard=jaccard,
                    priority=priority,
                )
            )
    edges.sort(key=lambda edge: edge.priority, reverse=True)
    return edges


def _calc_priority(user_a: User, user_b: User, now, jaccard: float) -> float:
    """
    Вычисляет приоритет пары: высокий Jaccard + давность последних матчей.

    Приоритет = jaccard + recency_bonus(user_a) + recency_bonus(user_b).

    Args:
        user_a (User): первый пользователь пары.
        user_b (User): второй пользователь пары.
        now: текущее время в МСК.
        jaccard (float): коэффициент Жаккара для пары.

    Returns:
        float: приоритет пары (чем выше, тем лучше для жадного алгоритма).
    """
    bonus_a = _recency_bonus(user_a, now)
    bonus_b = _recency_bonus(user_b, now)
    return jaccard + bonus_a + bonus_b


def _recency_bonus(user: User, now) -> float:
    """
    Возвращает бонус за длительное отсутствие матчей.

    Бонус увеличивается с количеством недель с последнего матча (до 0.2).

    Args:
        user (User): пользователь для расчёта бонуса.
        now: текущее время в МСК.

    Returns:
        float: бонус в диапазоне [0.0, 0.2].
    """
    last_match = user.last_match_at or user.last_pairing_at
    if not last_match:
        return 0.1
    delta = now - last_match
    weeks = delta.days / 7
    return min(0.2, weeks * 0.01)


def _select_pairs(edges: list[PairingEdge]) -> list[PairingEdge]:
    """
    Жадно строит паросочетание на основе отсортированных рёбер.

    Проходит по рёбрам в порядке убывания приоритета и добавляет пару,
    если оба пользователя ещё не использованы.

    Args:
        edges (list[PairingEdge]): отсортированный список потенциальных пар.

    Returns:
        list[PairingEdge]: список выбранных пар (максимальное паросочетание).
    """
    used_users: set[int] = set()
    pairs: list[PairingEdge] = []
    for edge in edges:
        if edge.user_a.id in used_users or edge.user_b.id in used_users:
            continue
        used_users.add(edge.user_a.id)
        used_users.add(edge.user_b.id)
        pairs.append(edge)
    return pairs


async def _persist_matches(
    session: AsyncSession,
    pairs: list[PairingEdge],
    now,
) -> list[Match]:
    """
    Сохраняет созданные пары в таблицу matches и обновляет last_pairing_at.

    Создаёт записи Match для каждой пары и обновляет last_pairing_at у обоих пользователей.

    Args:
        session (AsyncSession): активная сессия БД.
        pairs (list[PairingEdge]): список выбранных пар для сохранения.
        now: текущее время в МСК.

    Returns:
        list[Match]: список созданных объектов Match.
    """
    created: list[Match] = []
    for edge in pairs:
        user_a = edge.user_a
        user_b = edge.user_b
        match = Match(
            user_a_id=user_a.id,
            user_b_id=user_b.id,
            jaccard_score=edge.jaccard,
            created_at=now,
        )
        match.user_a = user_a
        match.user_b = user_b
        session.add(match)
        user_a.last_pairing_at = now
        user_b.last_pairing_at = now
        await session.flush()
        created.append(match)
    return created


async def _notify_new_matches(
    session: AsyncSession, bot: Bot, matches: list[Match]
) -> None:
    """
    Отправляет участникам сообщения о новой паре.

    Для каждого матча отправляет приглашение обоим участникам и сохраняет message_id.

    Args:
        session (AsyncSession): активная сессия БД для сохранения message_id.
        bot (Bot): экземпляр бота для отправки сообщений.
        matches (list[Match]): список созданных матчей.

    Returns:
        None: ничего не возвращает.
    """
    for match in matches:
        try:
            await _send_match_invite(session, bot, match, is_user_a=True)
            await _send_match_invite(session, bot, match, is_user_a=False)
        except Exception:
            logger.exception("Failed to notify users about match %s", match.id)


async def _send_match_invite(
    session: AsyncSession, bot: Bot, match: Match, *, is_user_a: bool
) -> None:
    """
    Отправляет сообщение одному участнику с приглашением на встречу.

    Сначала отправляет все фото партнёра (если есть), затем текстовое сообщение
    с анкетой партнёра и клавиатурой для ответа. Сохраняет message_id в БД.

    Args:
        session (AsyncSession): активная сессия БД для сохранения message_id.
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.
        is_user_a (bool): True если отправляем user_a, False если user_b.

    Returns:
        None: ничего не возвращает.
    """
    user = match.user_a if is_user_a else match.user_b
    partner = match.user_b if is_user_a else match.user_a
    if not user or not user.telegram_id or not partner:
        return

    # Отправляем фото партнёра, если есть
    if partner.photos_json and partner.photos_json.get("photos"):
        photos_list = partner.photos_json.get("photos", [])
        if photos_list:
            media_group = []
            for photo_data in photos_list:
                media_group.append(
                    InputMediaPhoto(media=photo_data["file_id"])
                )
            try:
                await bot.send_media_group(user.telegram_id, media=media_group)
            except Exception:
                # Если не удалось отправить группу, отправляем по одному
                for photo_data in photos_list:
                    await bot.send_photo(user.telegram_id, photo_data["file_id"])

    # Отправляем текстовое сообщение с анкетой
    partner_caption = _build_partner_caption(partner)
    text = (
        "☕️ Random Coffee\n\n"
        "Вам подобрали новую пару! Ознакомьтесь с анкетой:\n\n"
        f"{partner_caption}\n\n"
        "Если готовы познакомиться — нажмите кнопку ниже.\n"
        "Можно также пропустить участие на этой неделе."
    )
    sent_message = await bot.send_message(
        chat_id=user.telegram_id,
        text=text,
        reply_markup=kb_match_invitation(match.id),
        disable_web_page_preview=True,
    )
    # Сохраняем message_id для возможности удаления клавиатуры
    if is_user_a:
        match.invite_message_id_a = sent_message.message_id
    else:
        match.invite_message_id_b = sent_message.message_id
    await session.flush()  # Сохраняем message_id в БД


def _build_partner_caption(user: User) -> str:
    """
    Формирует текстовое описание партнёра для приглашения на встречу.

    Включает имя, username, возраст, биографию и интересы.

    Args:
        user (User): объект партнёра.

    Returns:
        str: отформатированное текстовое описание партнёра.
    """
    parts = []
    if user.name:
        parts.append(f"Имя: {user.name}")
    if user.username:
        parts.append(f"Telegram: @{user.username}")
    if user.age:
        parts.append(f"Возраст: {user.age}")
    if user.bio:
        parts.append(f"О себе: {user.bio}")
    interests = (user.interests_json or {}).get("interests", [])
    if interests:
        parts.append("Интересы: " + ", ".join(interests))
    return "\n".join(parts) if parts else "Новая пара"


async def _notify_no_pairs(bot: Bot, users: list[User]) -> None:
    """
    Сообщает пользователям об отсутствии пары в текущем раунде.

    Отправляет уведомление каждому пользователю, которому не удалось подобрать пару.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        users (list[User]): список пользователей без пары.

    Returns:
        None: ничего не возвращает.
    """
    if not users:
        return
    text = (
        "Сегодня состоялся круг Random Coffee, "
        "но по интересам не удалось подобрать пару. "
        "Вы автоматически участвуете в следующих раундах."
    )
    for user in users:
        if not user.telegram_id:
            continue
        try:
            await bot.send_message(user.telegram_id, text)
        except Exception:
            logger.exception("Failed to notify user %s about missing pair", user.id)

