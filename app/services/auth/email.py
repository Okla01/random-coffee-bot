"""
Бизнес-логика работы с электронной почтой и безопасностью авторизации.

Содержит функции для:
- отправки одноразовых паролей по электронной почте
- криптографически стойкой генерации OTP
- валидации адресов электронной почты с проверкой домена
- проверки существования email
- обработки ввода email при регистрации
"""

from __future__ import annotations

import ssl
import secrets
import re
from email.message import EmailMessage
from enum import Enum

import aiosmtplib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core import Settings
from app.database import User
from app.services.const import USER_STATUS_BLOCKED


async def send_otp_email(settings: Settings, to_email: str, otp_code: str) -> None:
    """
    Отправляет одноразовый пароль на адрес электронной почты.

    Формирует и отправляет электронное письмо с проверочным кодом через SMTP сервер
    с обязательным использованием TLS 1.2+. Письмо содержит код, время действия и примечание.
    Исключения предпочтительно обрабатывать на уровне обработчиков.

    Args:
        settings (Settings): объект конфигурации с параметрами SMTP.
        to_email (str): адрес электронной почты получателя.
        otp_code (str): одноразовый пароль для отправки.

    Returns:
        None: ничего не возвращает.

    Raises:
        aiosmtplib.SMTPException: если не удаётся отправить письмо через SMTP.
        OSError: если проблема с сетевым соединением.
    """
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg["Subject"] = "Код подтверждения для Random Coffee"
    msg.set_content(
        f"Ваш код подтверждения: {otp_code}\n"
        f"Срок действия: {settings.otp_ttl_seconds} секунд.\n"
        "Если вы не запрашивали код — просто игнорируйте это письмо."
    )

    # Создаём TLS-контекст с принудительным TLS 1.2+
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        start_tls=True,
        tls_context=ctx,  # Используем созданный контекст
        username=settings.smtp_user,
        password=settings.smtp_password,
        timeout=20,
    )


def generate_otp(length: int = 6) -> str:
    """
    Генерирует криптографически стойкий одноразовый пароль для подтверждения email.

    Создаёт строку цифр фиксированной длины, используя криптографически стойкий
    генератор случайных чисел (secrets). По умолчанию длина — 6 цифр.

    Args:
        length (int): длина OTP-кода в цифрах (по умолчанию 6).

    Returns:
        str: строка цифр требуемой длины.
    """
    return "".join(secrets.choice("0123456789") for _ in range(length))


def validate_email(
    email: str, regex: re.Pattern[str], allowed_domains: set[str]
) -> tuple[bool, str | None]:
    """
    Валидирует адрес электронной почты по регулярному выражению и списку доменов.

    Проверяет формат email на соответствие переданному regex-шаблону и проверяет,
    что домен входит в список разрешённых доменов (если список не пуст).

    Args:
        email (str): адрес электронной почты для валидации.
        regex (re.Pattern[str]): скомпилированное регулярное выражение для проверки формата.
        allowed_domains (set[str]): набор разрешённых доменов (если пусто, проверка доменов пропускается).

    Returns:
        tuple[bool, str | None]: кортеж (валидна_ли, сообщение_об_ошибке).
                                  Если валидна — (True, None), иначе (False, описание_ошибки).
    """
    if not regex.match(email):
        return False, "Некорректный формат e‑mail."
    if allowed_domains:
        try:
            domain = email.split("@", 1)[1].lower()
        except Exception:
            return False, "Некорректный формат e‑mail."
        if domain not in {d.lower() for d in allowed_domains}:
            return False, f"Домен @{domain} не разрешён."
    return True, None


class EmailResultType(str, Enum):
    """Типы результатов обработки email."""

    SUCCESS = "success"
    EXISTS = "exists"
    INVALID = "invalid"
    BLOCKED = "blocked"


async def check_email_exists(
    session: AsyncSession, email: str, exclude_telegram_id: int
) -> bool:
    """
    Проверяет, существует ли email у другого пользователя.

    Args:
        session (AsyncSession): сессия БД.
        email (str): email для проверки.
        exclude_telegram_id (int): Telegram ID пользователя, которого исключаем из проверки.

    Returns:
        bool: True, если email уже используется другим пользователем.
    """
    exists = (
        await session.execute(
            select(User).where(
                User.email == email, User.telegram_id != exclude_telegram_id
            )
        )
    ).scalar_one_or_none()
    return exists is not None


async def process_email_input(
    session: AsyncSession,
    settings: Settings,
    user: User,
    email: str,
    bot,
    sender_name: str,
    log_attempt_func,
    notify_admin_on_block_func,
    send_or_resend_otp_func,
) -> tuple[EmailResultType, str | None, str | None]:
    """
    Обрабатывает ввод email пользователем.

    Проверяет существование email, валидирует его, обрабатывает ошибки,
    обновляет состояние пользователя и отправляет OTP при успехе.

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        user (User): объект пользователя.
        email (str): введённый email.
        bot: объект бота для уведомления администратора.
        sender_name (str): полное имя пользователя.
        log_attempt_func: функция для логирования попыток.
        notify_admin_on_block_func: функция для уведомления администратора о блокировке.
        send_or_resend_otp_func: функция для отправки/переотправки OTP.

    Returns:
        tuple[EmailResultType, str | None, str | None]: (тип_результата, сообщение_об_ошибке, предупреждение_или_None).
    """
    await log_attempt_func(session, user.id, "email", email)

    if await check_email_exists(session, email, user.telegram_id):
        return (
            EmailResultType.EXISTS,
            "Этот email уже привязан к другому аккаунту. Если это ошибка — обратитесь к администратору.",
            None,
        )

    ok, err = validate_email(email, settings.email_regex, settings.allowed_domains)
    if not ok:
        user.email_attempts += 1
        user.stage = "verifying_email"
        if user.email_attempts > settings.email_max_attempts:
            user.status = USER_STATUS_BLOCKED
            user.stage = "verifying_email_error"
            await notify_admin_on_block_func(
                session,
                settings,
                user,
                "Слишком много неверных адресов",
                "email",
                bot,
                sender_name,
            )
            return (
                EmailResultType.BLOCKED,
                "Слишком много неверных адресов. Доступ заблокирован, администратор уведомлён, ожидайте решения.",
                None,
            )
        return (
            EmailResultType.INVALID,
            f"⚠️ {err}\nПопробуйте ещё раз (корпоративный e-mail).\nПопыток осталось: {settings.email_max_attempts - user.email_attempts + 1}",
            None,
        )

    user.email = email
    user.email_attempts = 0
    user.stage = "verifying_code"
    ok, warn = await send_or_resend_otp_func(session, settings, user)
    return EmailResultType.SUCCESS, None, warn
