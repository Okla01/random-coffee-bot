"""
Запуск раунда мэтчинга Random Coffee.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
import random

from aiogram import Bot
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Match, User
from app.database.utils import now_msk
from app.keyboards.kb_matching import kb_match_invitation
from app.services.const import USER_STATUS_ACTIVE
from app.services.matching.constants import MATCH_ACTIVE_STATUSES
from app.services.matching.settings import load_matching_settings
from app.services.matching.utils import (
    compute_jaccard,
    extract_interests_list,
)
from app.services.core.rate_limiter import rate_limited_send

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PairingEdge:
    """
    Представление потенциальной пары пользователей для мэтчинга.

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
    Главная процедура запуска раунда мэтчинга.

    Загружает настройки, фильтрует кандидатов, строит пары, сохраняет мэтчи
    и отправляет уведомления участникам.

    Args:
        session (AsyncSession): активная сессия БД.
        bot (Bot): экземпляр бота для отправки уведомлений.

    Returns:
        None: ничего не возвращает.
    """
    settings = await load_matching_settings(session)
    if not settings.matching_enabled:
        logger.info("Раунд мэтчинга пропущен: отключён в настройках.")
        return

    now = now_msk()
    candidates = await _load_candidates(session)
    if len(candidates) < 2:
        await _notify_no_pairs(bot, candidates)
        return

    existing_pairs = await _load_successful_pairs(session)
    edges = _build_pairing_edges(candidates, existing_pairs)

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
    await (
        session.commit()
    )  # Коммитим после отправки всех уведомлений и сохранения message_id

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
) -> list[User]:
    """
    Возвращает список пользователей, готовых к участию в новом раунде.

    Фильтрует пользователей по статусу, стадии профиля и отсутствию активных мэтчей.

    Args:
        session (AsyncSession): активная сессия БД.

    Returns:
        list[User]: список пользователей-кандидатов для мэтчинга.
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

    # Фильтруем только пользователей с telegram_id
    eligible: list[User] = []
    for user in users:
        if not user.telegram_id:
            continue
        eligible.append(user)
    return eligible


async def _load_users_with_active_matches(session: AsyncSession) -> set[int]:
    """
    Возвращает множество user_id, у которых есть активные мэтчи.

    Args:
        session (AsyncSession): активная сессия БД.

    Returns:
        set[int]: множество ID пользователей с активными мэтчами.
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


async def _load_successful_pairs(
    session: AsyncSession,
) -> set[frozenset[int]]:
    """
    Возвращает пары пользователей, которые уже имели мэтч когда-либо.

    Исключает повторные мэтчи между одними и теми же пользователями независимо
    от статуса предыдущего мэтча (completed, skipped, expired_timeout и т.д.)
    и времени создания мэтча. Пары, которые когда-либо имели мэтч, никогда
    не будут сопоставлены повторно.

    Args:
        session (AsyncSession): активная сессия БД.

    Returns:
        set[frozenset[int]]: множество пар (frozenset из двух user_id), которые
            уже имели мэтч когда-либо.
    """
    # Загружаем все пары из истории мэтчей
    stmt = select(Match.user_a_id, Match.user_b_id)
    
    result = await session.execute(stmt)
    return {
        frozenset((row[0], row[1]))
        for row in result.all()
        if row[0] is not None and row[1] is not None
    }


def _build_pairing_edges(
    candidates: list[User],
    existing_pairs: set[frozenset[int]],
) -> list[PairingEdge]:
    """
    Строит список рёбер (потенциальных пар) с весами.

    Вычисляет коэффициент Жаккара для каждой пары кандидатов и исключает пары,
    которые уже имели мэтч (любой статус). Рёбра с положительным Jaccard сортируются
    по убыванию, рёбра с нулевым Jaccard перемешиваются случайно.

    Args:
        candidates (list[User]): список кандидатов для мэтчинга.
        existing_pairs (set[frozenset[int]]): множество пар, которые уже имели мэтч.

    Returns:
        list[PairingEdge]: отсортированный по приоритету список потенциальных пар.
    """
    positive_edges: list[PairingEdge] = []
    zero_edges: list[PairingEdge] = []
    precomputed_interests = {
        user.id: extract_interests_list(user.interests_json) for user in candidates
    }

    for idx, user_a in enumerate(candidates):
        interests_a = precomputed_interests.get(user_a.id) or []
        for user_b in candidates[idx + 1 :]:
            pair_key = frozenset((user_a.id, user_b.id))
            if pair_key in existing_pairs:
                continue

            interests_b = precomputed_interests.get(user_b.id) or []
            jaccard = compute_jaccard(interests_a, interests_b)
            edge = PairingEdge(
                user_a=user_a,
                user_b=user_b,
                jaccard=jaccard,
                priority=jaccard,
            )
            if jaccard == 0:
                zero_edges.append(edge)
            else:
                positive_edges.append(edge)

    positive_edges.sort(key=lambda edge: edge.jaccard, reverse=True)
    random.shuffle(zero_edges)
    return positive_edges + zero_edges


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
    Сохраняет созданные пары в таблицу matches.

    Создаёт записи Match для каждой пары.
    
    ОПТИМИЗИРОВАНО: Один flush для всех мэтчей вместо flush на каждый мэтч.
    Это значительно ускоряет сохранение при большом количестве пар (2500+).

    Args:
        session (AsyncSession): активная сессия БД.
        pairs (list[PairingEdge]): список выбранных пар для сохранения.
        now: текущее время в МСК.

    Returns:
        list[Match]: список созданных объектов Match.
    """
    created: list[Match] = []
    
    # Собираем все изменения в памяти
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
        created.append(match)
    
    # Один flush для всех мэтчей (вместо flush на каждый мэтч)
    # При 2500 мэтчах это экономит ~12 секунд
    await session.flush()
    
    return created


async def _notify_new_matches(
    session: AsyncSession, bot: Bot, matches: list[Match]
) -> None:
    """
    Отправляет участникам сообщения о новой паре.

    Для каждого мэтча отправляет приглашение обоим участникам и сохраняет message_id.

    Args:
        session (AsyncSession): активная сессия БД для сохранения message_id.
        bot (Bot): экземпляр бота для отправки сообщений.
        matches (list[Match]): список созданных мэтчей.

    Returns:
        None: ничего не возвращает.
    """
    from app.services.core.config import Settings as AppSettings
    app_settings = AppSettings.load()

    for match in matches:
        try:
            await _send_match_invite(session, bot, match, app_settings, is_user_a=True)
            await _send_match_invite(session, bot, match, app_settings, is_user_a=False)
        except Exception:
            logger.exception("Не удалось уведомить пользователей о мэтче %s", match.id)


async def _send_match_invite(
    session: AsyncSession, bot: Bot, match: Match, app_settings, *, is_user_a: bool
) -> None:
    """
    Отправляет сообщение одному участнику с приглашением на встречу.

    Сначала отправляет все фото партнёра (если есть), затем текстовое сообщение
    с анкетой партнёра и клавиатурой для ответа. Сохраняет message_id в БД и устанавливает флаг notified.

    Args:
        session (AsyncSession): активная сессия БД для сохранения message_id.
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект мэтча с загруженными user_a и user_b.
        app_settings: настройки приложения (Settings) для доступа к photos_storage_chat_id.
        is_user_a (bool): True если отправляем user_a, False если user_b.

    Returns:
        None: ничего не возвращает.
    """
    user = match.user_a if is_user_a else match.user_b
    partner = match.user_b if is_user_a else match.user_a
    
    # Проверяем, что пользователь существует и является владельцем telegram_id
    if not user or not user.telegram_id or not partner:
        return
    
    # Проверяем, что текущий пользователь с этим telegram_id - тот же, что в мэтче
    expected_user_id = match.user_a_id if is_user_a else match.user_b_id
    if user.id != expected_user_id:
        logger.warning(
            "Несоответствие ID пользователя для мэтча %s: ожидалось %s, получено %s (telegram_id: %s)",
            match.id,
            expected_user_id,
            user.id,
            user.telegram_id,
        )
        return

    try:
        # Отправляем фото партнёра через централизованный сервис
        from app.services.photo import send_user_photos, has_photos

        if has_photos(partner):
            # send_user_photos автоматически обрабатывает ошибки file_id и обновляет их
            # Внутри send_user_photos используются bot.send_photo/send_media_group,
            # которые уже имеют rate limiting через middleware или другие механизмы
            await send_user_photos(
                bot, user.telegram_id, partner, app_settings, session=session
            )

        # Отправляем текстовое сообщение с анкетой
        partner_caption = _build_partner_caption(partner)
        text = (
            "☕️ Random Coffee\n\n"
            "Мэтч состоялся! Ознакомься с анкетой:\n\n"
            f"{partner_caption}\n\n"
            "Если готов(а) пойти с ним на кофе — нажми кнопку ниже. ☺️\n"
        )
        sent_message = await rate_limited_send(
            bot.send_message,
            chat_id=user.telegram_id,
            text=text,
            reply_markup=kb_match_invitation(match.id),
            disable_web_page_preview=True,
        )
        # Сохраняем message_id и устанавливаем флаг успешной отправки
        if is_user_a:
            match.last_message_id_a = sent_message.message_id
            match.notified_a = True
        else:
            match.last_message_id_b = sent_message.message_id
            match.notified_b = True
        await session.flush()
    except Exception as e:
        logger.exception("Не удалось отправить приглашение на мэтч пользователю %s (мэтч %s): %s", user.id, match.id, e)
        raise


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
        "Сегодня состоялся круг \"Random Coffee\", но, к сожалению, по твоим интересам не удалось найти «мэтч» 😔\n"
        "Однако ты автоматически участвуешь в следующих раундах🤜🏽🤛🏻"
    )   
    for user in users:
        if not user.telegram_id:
            continue
        try:
            await rate_limited_send(bot.send_message, user.telegram_id, text)
        except Exception:
            logger.exception("Не удалось уведомить пользователя %s об отсутствии пары", user.id)
