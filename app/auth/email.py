"""
Отправка одноразовых паролей по электронной почте.

Использует SMTP с TLS 1.2+ для безопасной отправки проверочных кодов
на адреса электронной почты пользователей. Ошибки передаются на уровень обработчиков.
"""

from __future__ import annotations

import ssl
from email.message import EmailMessage

import aiosmtplib

from app.core import Settings


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