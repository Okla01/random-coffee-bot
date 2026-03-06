"""
Сообщения пользователям в разных стадиях мэтчинга.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
import logging

from app.database import Match, User
from app.services.core.rate_limiter import rate_limited_send
from app.services.matching.constants import (
    MATCH_USER_RESPONSE_CONFIRM,
    MATCH_USER_RESPONSE_SKIP,
)

logger = logging.getLogger(__name__)


async def notify_match_ready(bot: Bot, match: Match, actor: User) -> None:
    """
    Отправляет подтверждение участнику, нажавшему «Готов выпить кофе».

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект мэтча.
        actor (User): пользователь, который нажал кнопку.

    Returns:
        None: ничего не возвращает.
    """
    if not actor.telegram_id:
        return
    await rate_limited_send(
        bot.send_message,
        actor.telegram_id,
        "Мы сообщили твоему коллеге, что ты готов(а) пойти с ним на кофе и теперь ждем его ответа!🙃",
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
    await rate_limited_send(
        bot.send_message,
        user.telegram_id,
        "Ты пропустил участие на этой неделе. "
        "Ты сможешь снова участвовать в следующих раундах.",
    )


async def notify_match_skip_partner(bot: Bot, match: Match, skipper: User) -> None:
    """
    Уведомляет партнёра о том, что вторая сторона пропустила раунд.

    Удаляет клавиатуру из сообщения с приглашением у партнёра.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект мэтча с загруженными user_a и user_b.
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

    await rate_limited_send(
        bot.send_message,
        partner.telegram_id,
        "К сожалению, твоя пара решила пропустить участие на этой неделе. "
        "Ты автоматически попадёшь в следующий раунд.",
    )


async def notify_match_user_deleted(bot: Bot, match: Match, deleted_user: User) -> None:
    """
    Уведомляет партнёра о том, что вторая сторона удалила анкету.

    Удаляет клавиатуру из сообщения с приглашением у партнёра.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект мэтча с загруженными user_a и user_b.
        deleted_user (User): пользователь, который удалил анкету.

    Returns:
        None: ничего не возвращает.
    """
    partner = _get_partner(match, deleted_user)
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

    await rate_limited_send(
        bot.send_message,
        partner.telegram_id,
        "К сожалению, твоя пара удалила анкету. "
        "Ты автоматически попадёшь в следующий раунд.",
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
    await rate_limited_send(
        bot.send_message,
        user.telegram_id,
        "Сегодня состоялся круг \"Random Coffee\", но, к сожалению, по твоим интересам не удалось найти «мэтч» 😔\n"
        "Однако ты автоматически участвуешь в следующих раундах🤜🏽🤛🏻"
    )


async def notify_match_scheduled(bot: Bot, match: Match) -> None:
    """
    Уведомляет обоих участников о том, что они совпали.

    Вызывается когда оба участника нажали «Готов выпить кофе» и мэтч
    перешёл в статус matched. Пользователи теперь сами договариваются о времени в ЛС.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект мэтча с загруженными user_a и user_b.

    Returns:
        None: ничего не возвращает.
    """
    # Отправляем персонализированное сообщение каждому участнику
    for user in (match.user_a, match.user_b):
        if not user or not user.telegram_id:
            continue

        partner = _get_partner(match, user)
        partner_username = (
            f"@{partner.username}" if partner and partner.username else "твоей парой"
        )

        text = (
            "🎉 Отличные новости!\n"
            f"Вы совпали с {partner_username}!\n"
            "Напишите коллеге для выбора времени встречи!"
        )

        await rate_limited_send(bot.send_message, user.telegram_id, text)


async def notify_match_timeout(bot: Bot, match: Match) -> None:
    """Notify users when response window expires for a match."""
    await remove_match_keyboards(bot, match)

    timeout_text = (
        "\u0412\u0440\u0435\u043c\u044f \u043d\u0430 \u0441\u043e\u0433\u043b\u0430\u0441\u043e\u0432\u0430\u043d\u0438\u0435 \u0432\u0441\u0442\u0440\u0435\u0447\u0438 \u0438\u0441\u0442\u0435\u043a\u043b\u043e. "
        "\u0412\u044b \u0441\u043c\u043e\u0436\u0435\u0442\u0435 \u0443\u0447\u0430\u0441\u0442\u0432\u043e\u0432\u0430\u0442\u044c \u0432 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0445 \u0440\u0430\u0443\u043d\u0434\u0430\u0445."
    )
    partner_skipped_text = (
        "\u041a \u0441\u043e\u0436\u0430\u043b\u0435\u043d\u0438\u044e, \u0442\u0432\u043e\u044f \u043f\u0430\u0440\u0430 \u0440\u0435\u0448\u0438\u043b\u0430 "
        "\u043f\u0440\u043e\u043f\u0443\u0441\u0442\u0438\u0442\u044c \u0443\u0447\u0430\u0441\u0442\u0438\u0435 \u043d\u0430 \u044d\u0442\u043e\u0439 \u043d\u0435\u0434\u0435\u043b\u0435. "
        "\u0422\u044b \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438 \u043f\u043e\u043f\u0430\u0434\u0451\u0448\u044c \u0432 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 \u0440\u0430\u0443\u043d\u0434."
    )

    active_user = None
    ignored_user = None

    if (
        match.user_a
        and match.user_b
        and match.user_a_response == MATCH_USER_RESPONSE_CONFIRM
        and match.user_b_response in {None, MATCH_USER_RESPONSE_SKIP}
    ):
        active_user = match.user_a
        ignored_user = match.user_b
    elif (
        match.user_a
        and match.user_b
        and match.user_b_response == MATCH_USER_RESPONSE_CONFIRM
        and match.user_a_response in {None, MATCH_USER_RESPONSE_SKIP}
    ):
        active_user = match.user_b
        ignored_user = match.user_a

    if active_user and ignored_user:
        if active_user.telegram_id:
            await rate_limited_send(
                bot.send_message,
                active_user.telegram_id,
                partner_skipped_text,
            )
        if ignored_user.telegram_id:
            await rate_limited_send(
                bot.send_message,
                ignored_user.telegram_id,
                timeout_text,
            )

        logger.info(
            "timeout notifications sent (split): match_id=%s active_user=%s ignored_user=%s",
            match.id,
            getattr(active_user, "id", None),
            getattr(ignored_user, "id", None),
        )
        return

    await _broadcast(bot, match, timeout_text)
    logger.info(
        "timeout notifications sent (broadcast): match_id=%s user_a_response=%s user_b_response=%s",
        match.id,
        match.user_a_response,
        match.user_b_response,
    )

async def notify_match_reminder(
    bot: Bot, match: Match, stage: str, users_to_remind: list[User] | None = None
) -> None:
    """
    Отправляет напоминание указанным пользователям в зависимости от текущей стадии мэтча.

    Если users_to_remind не указан, отправляет напоминание обоим участникам (обратная совместимость).

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект мэтча с загруженными user_a и user_b.
        stage (str): текущая стадия мэтча (pending_response, waiting_confirm).
        users_to_remind (list[User] | None): список пользователей, которым нужно отправить напоминание.
            Если None, отправляет обоим участникам.

    Returns:
        None: ничего не возвращает.
    """
    if stage == "pending_response":
        text = (
            "Напоминаем, что у вас есть пара для Random Coffee. "
            "Нажмите «Готов выпить кофе» или «Пропустить на этой неделе»."
        )
    else:
        # Неизвестная стадия, пропускаем
        return

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
    Отправляет текстовое сообщение обоим участникам мэтча.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект мэтча с загруженными user_a и user_b.
        text (str): текст сообщения.

    Returns:
        None: ничего не возвращает.
    """
    for user in (match.user_a, match.user_b):
        if user and user.telegram_id:
            await rate_limited_send(bot.send_message, user.telegram_id, text)


def _get_partner(match: Match, actor: User) -> User | None:
    """
    Возвращает партнёра участника мэтча.

    Args:
        match (Match): объект мэтча с загруженными user_a и user_b.
        actor (User): участник мэтча, для которого нужно найти партнёра.

    Returns:
        User | None: партнёр участника или None, если actor не является участником мэтча.
    """
    if actor.id == match.user_a_id:
        return match.user_b
    if actor.id == match.user_b_id:
        return match.user_a
    return None


async def remove_match_keyboards(bot: Bot, match: Match) -> None:
    """
    Удаляет inline-клавиатуры по сохранённым message_id у обоих участников мэтча.

    Args:
        bot (Bot): экземпляр бота для отправки сообщений.
        match (Match): объект мэтча с загруженными user_a и user_b.
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
                    "Не удалось удалить клавиатуру для пользователя %s сообщения %s: %s",
                    getattr(user, "id", None),
                    message_id,
                    exc,
                )
        except Exception:
            logger.exception(
                "Не удалось удалить клавиатуру для пользователя %s сообщения %s",
                getattr(user, "id", None),
                message_id,
            )
