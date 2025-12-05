"""
Сообщения пользователям в разных стадиях матчинга.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
import logging

from app.database import Match, User
from app.keyboards.kb_matching import (
    kb_match_confirm_prompt,
    kb_meeting_feedback,
)

logger = logging.getLogger(__name__)


async def notify_match_ready(bot: Bot, match: Match, actor: User) -> None:
    """
    Отправляет подтверждение участнику, нажавшему «Готов познакомиться».

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча.
        actor (User): пользователь, который нажал кнопку.

    Returns:
        None: ничего не возвращает.
    """
    if not actor.telegram_id:
        return
    await bot.send_message(
        actor.telegram_id,
        "Мы зафиксировали, что вы готовы познакомиться. Ждём ответа вашей пары.",
    )


async def notify_waiting_partner_ready(bot: Bot, match: Match) -> None:
    """
    Сообщает обоим участникам о взаимном согласии и отправляет календарь выбора времени.

    Вызывается когда оба участника нажали «Готов познакомиться» и матч перешёл
    в статус waiting_slots.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.

    Returns:
        None: ничего не возвращает.
    """
    for user in (match.user_a, match.user_b):
        if not user or not user.telegram_id:
            continue
        partner = _get_partner(match, user)
        partner_hint = _format_partner_hint(partner)
        text = (
            "🎉 Отличные новости!\n"
            f"Вы совпали с {partner_hint}.\n"
            "Мы открыли для вас календарь выбора времени. "
            "Отметьте несколько удобных дней и интервалов на ближайшие две недели."
        )
        await bot.send_message(
            user.telegram_id,
            text,
        )


async def notify_match_skip_self(bot: Bot, user: User) -> None:
    """
    Отправляет сообщение пользователю, который пропустил раунд.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        user (User): пользователь, который пропустил раунд.

    Returns:
        None: ничего не возвращает.
    """
    if not user.telegram_id:
        return
    await bot.send_message(
        user.telegram_id,
        "Вы пропустили участие на этой неделе. "
        "Вы сможете снова участвовать в следующих раундах.",
    )


async def notify_match_skip_partner(bot: Bot, match: Match, skipper: User) -> None:
    """
    Уведомляет партнёра о том, что вторая сторона пропустила раунд.

    Удаляет клавиатуру из сообщения с приглашением у партнёра.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.
        skipper (User): пользователь, который пропустил раунд.

    Returns:
        None: ничего не возвращает.
    """
    partner = _get_partner(match, skipper)
    if not partner or not partner.telegram_id:
        return

    # Удаляем клавиатуру из сообщения с приглашением у партнёра
    partner_message_id = None
    if partner.id == match.user_a_id and match.last_message_id_a:
        partner_message_id = match.last_message_id_a
    elif partner.id == match.user_b_id and match.last_message_id_b:
        partner_message_id = match.last_message_id_b

    if partner_message_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=partner.telegram_id,
                message_id=partner_message_id,
                reply_markup=None,
            )
        except Exception:
            # Игнорируем ошибки (сообщение могло быть удалено или изменено)
            pass

    await bot.send_message(
        partner.telegram_id,
        "К сожалению, ваша пара решила пропустить участие на этой неделе. "
        "Вы автоматически попадёте в следующий раунд.",
    )


async def notify_match_not_found(bot: Bot, user: User) -> None:
    """
    Уведомляет пользователя о том, что в текущем раунде ему не подобрали пару.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        user (User): пользователь без пары.

    Returns:
        None: ничего не возвращает.
    """
    if not user.telegram_id:
        return
    await bot.send_message(
        user.telegram_id,
        "Сегодня состоялся раунд Random Coffee, "
        "но не удалось найти подходящую пару. "
        "Мы попробуем снова в следующем раунде.",
    )


async def notify_match_slots_saved(bot: Bot, user: User) -> None:
    """
    Уведомляет пользователя о сохранении выбранных временных слотов.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        user (User): пользователь, который сохранил слоты.

    Returns:
        None: ничего не возвращает.
    """
    if not user.telegram_id:
        return
    await bot.send_message(
        user.telegram_id,
        "Ваши варианты времени сохранены. Ждём выбор времени от вашей пары.",
    )


async def notify_no_common_slot(bot: Bot, match: Match) -> None:
    """
    Уведомляет обоих участников о том, что не найдено пересечение временных слотов.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.

    Returns:
        None: ничего не возвращает.
    """
    text = (
        "К сожалению, не удалось найти время, которое подошло бы вам обоим. "
        "Вы сможете участвовать в следующих раундах."
    )
    await _broadcast(bot, match, text)


async def notify_waiting_confirm(
    bot: Bot, match: Match, start_dt: datetime, end_dt: datetime
) -> dict[int, int]:
    """
    Уведомляет обоих участников о найденном пересечении и запрашивает подтверждение.

    Отправляет сообщение с предложенным временем встречи и клавиатуру с кнопками
    «Подтвердить» / «Назначить заново».

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.
        start_dt (datetime): дата и время начала встречи.
        end_dt (datetime): дата и время окончания встречи.

    Returns:
        dict[int, int]: отображение user_id -> message_id отправленного сообщения.
    """
    text = (
        "✅ Обоим подходит "
        f"{start_dt:%d.%m %H:%M} – {end_dt:%H:%M}.\n"
        "Подтвердить встречу или выбрать время заново?"
    )
    message_ids: dict[int, int] = {}
    markup = kb_match_confirm_prompt(match.id)
    for user in (match.user_a, match.user_b):
        if not user or not user.telegram_id:
            continue
        sent = await bot.send_message(
            user.telegram_id,
            text,
            reply_markup=markup,
        )
        if user.id:
            message_ids[user.id] = sent.message_id
    return message_ids


async def notify_match_confirm_waiting(bot: Bot, user: User) -> None:
    """
    Уведомляет пользователя о том, что его подтверждение получено, ожидается подтверждение партнёра.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        user (User): пользователь, который подтвердил встречу.

    Returns:
        None: ничего не возвращает.
    """
    if not user.telegram_id:
        return
    await bot.send_message(
        user.telegram_id,
        "Ваше подтверждение получено. Ждём подтверждения от вашей пары.",
    )


async def notify_match_scheduled(bot: Bot, match: Match) -> None:
    """
    Уведомляет обоих участников о том, что встреча успешно назначена.

    Вызывается когда оба участника подтвердили предложенное время и матч
    перешёл в статус scheduled.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b и установленным meeting_start_at.

    Returns:
        None: ничего не возвращает.
    """
    if not match.meeting_start_at:
        return

    # Отправляем персонализированное сообщение каждому участнику
    for user in (match.user_a, match.user_b):
        if not user or not user.telegram_id:
            continue

        partner = _get_partner(match, user)
        partner_username = (
            f"@{partner.username}" if partner and partner.username else "вашей парой"
        )

        text = (
            f"⏰ Встреча назначена с {partner_username}!\n"
            f"Дата и время: {match.meeting_start_at:%d.%m %H:%M}.\n"
            "Приятного общения!"
        )
        await bot.send_message(user.telegram_id, text)


async def notify_match_reschedule_partner(
    bot: Bot, match: Match, initiator: User
) -> None:
    """
    Уведомляет партнёра о том, что вторая сторона решила выбрать время заново.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.
        initiator (User): пользователь, который инициировал повторный выбор времени.

    Returns:
        None: ничего не возвращает.
    """
    partner = _get_partner(match, initiator)
    if not partner or not partner.telegram_id:
        return
    await bot.send_message(
        partner.telegram_id,
        "Ваша пара решила выбрать время заново.\n"
        "Давайте выберем удобное время для встречи.\n"
        "Календарь открыт повторно — отметьте новые слоты.",
    )


async def notify_match_reschedule_prompt(bot: Bot, match: Match) -> None:
    """
    Отправляет обоим участникам календарь для повторного выбора времени встречи.

    Вызывается после того, как один из участников нажал «Назначить заново».

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.

    Returns:
        None: ничего не возвращает.
    """
    for user in (match.user_a, match.user_b):
        if not user or not user.telegram_id:
            continue


async def notify_meeting_started(bot: Bot, match: Match) -> None:
    """
    Уведомляет обоих участников о наступлении времени встречи.

    Вызывается автоматической джобой, когда meeting_start_at <= текущее время.
    Отправляет сообщение с кнопками для оценки встречи.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.

    Returns:
        None: ничего не возвращает.
    """
    text = (
        "👋 Время вашей встречи наступило. "
        "Хорошего общения!\n"
        "После встречи вы можете оценить как всё прошло!"
    )
    markup = kb_meeting_feedback(match.id)

    for user in (match.user_a, match.user_b):
        if not user or not user.telegram_id:
            continue
        try:
            await bot.send_message(
                user.telegram_id,
                text,
                reply_markup=markup,
            )
        except Exception as e:
            logger.exception(
                "Failed to send meeting started notification to user %s: %s", user.id, e
            )


async def notify_match_timeout(bot: Bot, match: Match) -> None:
    """
    Уведомляет обоих участников об истечении времени на согласование встречи.

    Вызывается автоматической джобой при переводе матча в статус expired_timeout.
    Перед отправкой уведомления удаляет клавиатуры из старых сообщений.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.

    Returns:
        None: ничего не возвращает.
    """
    # Удаляем клавиатуры из старых сообщений перед отправкой уведомления
    await remove_match_keyboards(bot, match)

    text = (
        "Время на согласование встречи истекло. "
        "Вы сможете участвовать в следующих раундах."
    )
    await _broadcast(bot, match, text)


async def notify_match_reminder(
    bot: Bot, match: Match, stage: str, users_to_remind: list[User] | None = None
) -> None:
    """
    Отправляет напоминание указанным пользователям в зависимости от текущей стадии матча.

    Если users_to_remind не указан, отправляет напоминание обоим участникам (обратная совместимость).

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.
        stage (str): текущая стадия матча (pending_response, waiting_slots, waiting_confirm).
        users_to_remind (list[User] | None): список пользователей, которым нужно отправить напоминание.
            Если None, отправляет обоим участникам.

    Returns:
        None: ничего не возвращает.
    """
    if stage == "pending_response":
        text = (
            "Напоминаем, что у вас есть пара для Random Coffee. "
            "Нажмите «Готов познакомиться» или «Пропустить на этой неделе»."
        )
    elif stage == "waiting_slots":
        text = "Напоминаем, что нужно выбрать удобные дни и время для встречи."
    else:  # waiting_confirm
        text = (
            "Напоминаем, что нужно подтвердить предложенное время "
            "или выбрать «Назначить заново»."
        )

    if users_to_remind is None:
        # Обратная совместимость: отправляем обоим участникам
        await _broadcast(bot, match, text)
    else:
        # Отправляем только указанным пользователям
        for user in users_to_remind:
            if user and user.telegram_id:
                await bot.send_message(user.telegram_id, text)


async def _broadcast(bot: Bot, match: Match, text: str) -> None:
    """
    Отправляет текстовое сообщение обоим участникам матча.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.
        text (str): текст сообщения.

    Returns:
        None: ничего не возвращает.
    """
    for user in (match.user_a, match.user_b):
        if user and user.telegram_id:
            await bot.send_message(user.telegram_id, text)


async def _broadcast_with_markup(bot: Bot, match: Match, text: str, markup) -> None:
    """
    Отправляет текстовое сообщение с клавиатурой обоим участникам матча.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.
        text (str): текст сообщения.
        markup: inline-клавиатура для сообщения.

    Returns:
        None: ничего не возвращает.
    """
    for user in (match.user_a, match.user_b):
        if user and user.telegram_id:
            await bot.send_message(user.telegram_id, text, reply_markup=markup)


def _get_partner(match: Match, actor: User) -> User | None:
    """
    Возвращает партнёра участника матча.

    Args:
        match (Match): объект матча с загруженными user_a и user_b.
        actor (User): участник матча, для которого нужно найти партнёра.

    Returns:
        User | None: партнёр участника или None, если actor не является участником матча.
    """
    if actor.id == match.user_a_id:
        return match.user_b
    if actor.id == match.user_b_id:
        return match.user_a
    return None


def _format_partner_hint(partner: User | None) -> str:
    """
    Форматирует краткое описание партнёра для использования в сообщениях.

    Args:
        partner (User | None): объект партнёра или None.

    Returns:
        str: строка с именем и username партнёра, или "вашей парой" если партнёр не указан.
    """
    if not partner:
        return "вашей парой"
    parts = []
    if partner.name:
        parts.append(partner.name)
    if partner.username:
        parts.append(f"@{partner.username}")
    if not parts and partner.telegram_id:
        parts.append(f"tg://user?id={partner.telegram_id}")
    return " ".join(parts)


async def remove_match_keyboards(bot: Bot, match: Match) -> None:
    """
    Удаляет inline-клавиатуры по сохранённым message_id у обоих участников матча.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект матча с загруженными user_a и user_b.
    """
    for user, message_id_attr in (
        (match.user_a, "last_message_id_a"),
        (match.user_b, "last_message_id_b"),
    ):
        message_id = getattr(match, message_id_attr, None)
        if not user or not user.telegram_id or not message_id:
            continue
        try:
            await bot.edit_message_reply_markup(
                chat_id=user.telegram_id,
                message_id=message_id,
                reply_markup=None,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                logger.exception(
                    "Failed to remove keyboard for user %s message %s: %s",
                    getattr(user, "id", None),
                    message_id,
                    exc,
                )
        except Exception:
            logger.exception(
                "Failed to remove keyboard for user %s message %s",
                getattr(user, "id", None),
                message_id,
            )
