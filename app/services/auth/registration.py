"""
Бизнес-логика регистрации через корпоративный email и OTP.

Содержит функции для:
- логирования попыток ввода учётных данных
- отправки и переотправки OTP с соблюдением ограничений
- уведомления администратора о блокировке пользователя
- обработки ввода email и OTP
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from enum import Enum

from sqlalchemy import select, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core import Settings
from app.services.auth.email import send_otp_email, generate_otp
from app.database import User, Otp, AuthAttempt, AdminLog
from app.services.const import USER_STATUS_BLOCKED
from app.database.utils import now_msk, ensure_aware_msk
from app.keyboards.kb_admin import kb_admin_decision


class OtpResultType(str, Enum):
    """Типы результатов обработки OTP."""
    SUCCESS = "success"
    INVALID_FORMAT = "invalid_format"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    ALREADY_USED = "already_used"
    WRONG_CODE = "wrong_code"
    BLOCKED = "blocked"


async def log_attempt(
    session: AsyncSession, user_id: int, typ: str, value: str
) -> None:
    """
    Логирует попытку ввода учётных данных и удаляет старые записи.

    Сохраняет новую попытку ввода (email или OTP) и оставляет только последние 3 записи
    для этого пользователя и типа. Старые записи удаляются из БД.

    Args:
        session (AsyncSession): сессия БД.
        user_id (int): ID пользователя в БД.
        typ (str): тип попытки ('email' или 'otp').
        value (str): введённое значение.

    Returns:
        None: ничего не возвращает.
    """
    # Сохраним попытку и оставим только последние 3 для данного user_id/type
    session.add(AuthAttempt(user_id=user_id, type=typ, value=value))
    await session.flush()
    # Оставляем только последние 3 записи; удаляем более старые
    q = (
        select(AuthAttempt)
        .where(AuthAttempt.user_id == user_id, AuthAttempt.type == typ)
        .order_by(desc(AuthAttempt.ts))
    )
    rows = list((await session.execute(q)).scalars())
    if len(rows) > 3:
        # Удаляем старые записи через statement-based удаление
        old_ids = [row.id for row in rows[3:]]
        if old_ids:
            stmt = delete(AuthAttempt).where(AuthAttempt.id.in_(old_ids))
            await session.execute(stmt)


async def get_last_attempts(
    session: AsyncSession, user_id: int, typ: str, limit: int = 3
) -> list[AuthAttempt]:
    """
    Получает последние N попыток ввода учётных данных.

    Извлекает последние (ограниченное количество) попытки для пользователя и типа данных,
    отсортированные по времени в обратном порядке.

    Args:
        session (AsyncSession): сессия БД.
        user_id (int): ID пользователя в БД.
        typ (str): тип попытки ('email' или 'otp').
        limit (int): максимальное количество записей для возврата (по умолчанию 3).

    Returns:
        list[AuthAttempt]: список последних попыток.
    """
    q = (
        select(AuthAttempt)
        .where(AuthAttempt.user_id == user_id, AuthAttempt.type == typ)
        .order_by(desc(AuthAttempt.ts))
        .limit(limit)
    )
    return list((await session.execute(q)).scalars())


async def send_or_resend_otp(
    session: AsyncSession, settings: Settings, user: User
) -> tuple[bool, str | None]:
    """
    Создаёт новый OTP или переотправляет существующий с соблюдением ограничений.

    Если активный неиспользованный OTP существует, попытается переотправить его
    (соблюдая cooldown 120 сек и лимит ≤3 переотправок). Если кода нет или он истёк,
    создаёт новый код с TTL из settings. Отправляет код на email пользователя.

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        user (User): объект пользователя с заполненным email.

    Returns:
        tuple[bool, str | None]: (успех_ли_операция, предупреждение_или_None).

    Raises:
        Exception: если ошибка при отправке email.
    """
    now = now_msk()

    existing = (
        (
            await session.execute(
                select(Otp)
                .where(Otp.user_id == user.id, Otp.used_at.is_(None))
                .order_by(desc(Otp.created_at))
            )
        )
        .scalars()
        .first()
    )

    warn: str | None = None

    if existing:
        ex_expires_at = ensure_aware_msk(existing.expires_at)
        ex_last_sent_at = ensure_aware_msk(existing.last_sent_at)

        if ex_expires_at and ex_expires_at > now:
            if (
                ex_last_sent_at
                and (ex_last_sent_at + timedelta(seconds=settings.otp_cooldown_seconds))
                > now
            ):
                warn = "Повторная отправка возможна не чаще, чем раз в 120 секунд."
            else:
                if existing.resend_count >= settings.resend_max_per_session:
                    warn = "Достигнут лимит переотправок для этой сессии."
                else:
                    await send_otp_email(settings, user.email, existing.code)
                    existing.resend_count += 1
                    existing.last_sent_at = now
                    warn = "Код отправлен повторно. Проверьте вашу почту и введите код:"
            return True, warn

    code = generate_otp(6)
    session_id = uuid.uuid4().hex[:8]
    expires = now + timedelta(seconds=settings.otp_ttl_seconds)

    otp = Otp(
        user_id=user.id,
        code=code,
        session_id=session_id,
        resend_count=0,
        last_sent_at=now,
        created_at=now,
        expires_at=expires,
    )
    session.add(otp)
    await send_otp_email(settings, user.email, code)
    return True, warn


async def notify_admin_on_block(
    session: AsyncSession,
    settings: Settings,
    user: User,
    reason: str,
    typ: str,
    bot,
    sender_name: str,
) -> None:
    """
    Уведомляет администратора о блокировке пользователя.

    Логирует событие в admin_log с причиной и последними попытками,
    затем отправляет сообщение в admin_chat_id с информацией о пользователе,
    последними данными и кнопками для принятия решения (блокировать/разблокировать).

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация (содержит admin_chat_id).
        user (User): объект пользователя для блокировки.
        reason (str): причина блокировки.
        typ (str): тип попытки ('email' или 'otp').
        bot: объект бота для отправки сообщения.
        sender_name (str): полное имя пользователя для уведомления.

    Returns:
        None: ничего не возвращает.
    """
    if not settings.admin_chat_id:
        return
    attempts = await get_last_attempts(session, user.id, typ)
    payload = {
        "user_id": user.id,
        "reason": reason,
        "type": typ,
        "attempts": [a.value for a in attempts],
    }
    session.add(
        AdminLog(
            admin_id=0,
            action="auth_block_request",
            payload=payload,
        )
    )
    # Сохраняем запись в БД, чтобы в admin_log было видно заявку
    await session.commit()
    # Отправляем сообщение в админ-чат с последними попытками и кнопками для принятия решения
    try:
        text = (
            f"❗️Неудачный вход\n"
            f"👤: {sender_name}\n"
            f"🔗: {'@' + user.username if user.username else 'нет username'}\n"
            f"🆔: {user.telegram_id}\n\n"
            f"Причина: {reason}\n"
            f"Последние {typ} попытки: {', '.join([a.value for a in attempts]) if attempts else 'нет данных'}"
        )
        await bot.send_message(
            chat_id=settings.admin_chat_id,
            text=text,
            reply_markup=kb_admin_decision(user.id),
        )
    except Exception:
        # Не фейлим основную операцию из-за ошибки отправки нотификации
        pass


async def get_latest_otp(
    session: AsyncSession, user_id: int
) -> Otp | None:
    """
    Получает последний OTP для пользователя.

    Args:
        session (AsyncSession): сессия БД.
        user_id (int): ID пользователя в БД.

    Returns:
        Otp | None: последний OTP или None, если не найден.
    """
    otp_row = (
        (
            await session.execute(
                select(Otp)
                .where(Otp.user_id == user_id)
                .order_by(desc(Otp.created_at))
            )
        )
        .scalars()
        .first()
    )
    return otp_row




async def process_otp_input(
    session: AsyncSession,
    settings: Settings,
    user: User,
    code: str,
    bot,
    sender_name: str,
) -> tuple[OtpResultType, str | None]:
    """
    Обрабатывает ввод OTP-кода пользователем.

    Проверяет формат кода, получает OTP из БД, валидирует его,
    обрабатывает ошибки и обновляет состояние пользователя при успехе.

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        user (User): объект пользователя.
        code (str): введённый OTP-код.
        bot: объект бота для уведомления администратора.
        sender_name (str): полное имя пользователя.

    Returns:
        tuple[OtpResultType, str | None]: (тип_результата, сообщение_об_ошибке_или_None).
    """
    if not code.isdigit() or not (4 <= len(code) <= 8):
        return OtpResultType.INVALID_FORMAT, "Ожидаю код из письма (6 символов):"

    await log_attempt(session, user.id, "otp", code)

    now = now_msk()
    otp_row = await get_latest_otp(session, user.id)

    if not otp_row:
        return OtpResultType.NOT_FOUND, f"Код не найден. Отправить новый код на {user.email}?"

    exp = ensure_aware_msk(otp_row.expires_at)
    used_at = ensure_aware_msk(otp_row.used_at)

    if not exp or exp <= now:
        return OtpResultType.EXPIRED, f"Код истёк. Отправить новый код на {user.email}?"

    if used_at:
        return OtpResultType.ALREADY_USED, "Код уже был использован. Запросите новый."

    if code != otp_row.code:
        user.otp_attempts += 1
        user.stage = "verifying_code"
        if user.otp_attempts > settings.otp_max_attempts:
            user.status = USER_STATUS_BLOCKED
            user.stage = "verifying_code_error"
            await notify_admin_on_block(
                session,
                settings,
                user,
                "Слишком много неверных OTP-кодов",
                "otp",
                bot,
                sender_name,
            )
            return (
                OtpResultType.BLOCKED,
                "Слишком много неверных попыток. Доступ заблокирован, администратор уведомлён, ожидайте решения.",
            )
        return (
            OtpResultType.WRONG_CODE,
            f"Неверный код. Попробуйте ещё раз или запросите новый.\nПопыток осталось: {settings.otp_max_attempts - user.otp_attempts + 1}",
        )

    # УСПЕХ: обновляем состояние пользователя
    otp_row.used_at = now
    user.stage = "profile_name"
    user.email_attempts = 0
    user.otp_attempts = 0
    return OtpResultType.SUCCESS, None


async def check_email_change_allowed(
    session: AsyncSession, user_id: int, cooldown_seconds: int = 120
) -> tuple[bool, int | None]:
    """
    Проверяет, можно ли сменить email (прошло ли достаточно времени с момента создания OTP).

    Args:
        session (AsyncSession): сессия БД.
        user_id (int): ID пользователя в БД.
        cooldown_seconds (int): минимальное время в секундах с момента создания OTP (по умолчанию 120).

    Returns:
        tuple[bool, int | None]: (разрешено_ли, оставшееся_время_в_секундах_или_None).
    """
    now = now_msk()
    otp_row = await get_latest_otp(session, user_id)

    if not otp_row:
        return True, None

    created_at = ensure_aware_msk(otp_row.created_at)
    if created_at and (created_at + timedelta(seconds=cooldown_seconds)) > now:
        time_remaining = int(
            (created_at + timedelta(seconds=cooldown_seconds) - now).total_seconds()
        )
        return False, time_remaining

    return True, None

