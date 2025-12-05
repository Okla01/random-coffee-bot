"""
Загрузка конфигурации из .env:
- ALLOWED_DOMAINS (список доменов),
- EMAIL_REGEX (регулярка для e-mail),
- ADMIN_IDS/ADMIN_CHAT_ID (поддерживается также ADMIN_CHAT_ID_NOTIFICATION),
- SMTP_* для отправки писем,
- лимиты OTP/попыток,
- общие настройки.

Файл читает .env через python-dotenv (load_dotenv).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Pattern, Set, List

from dotenv import load_dotenv

from app.services.profile.banned_words import load_banned_words


def _parse_list(raw: str) -> List[str]:
    """
    Парсит строку в список значений.

    Пробует распарсить строку как JSON-массив, а если это не удаётся,
    разбивает по запятым, пробелам и другим разделителям.

    Args:
        raw (str): исходная строка для парсинга.

    Returns:
        list[str]: список распарсенных и тримленных значений.
    """
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    parts = re.split(r"[,\s]+", raw)
    return [x.strip() for x in parts if x.strip()]


@dataclass(frozen=True)
class Settings:
    """Иммутабельные настройки приложения."""

    # Bot / Admin
    # Бот и администрирование
    bot_token: str
    admin_ids: Set[int]
    admin_chat_id: int | None

    # Email checks
    # Проверка email
    email_regex_str: str
    email_regex: Pattern[str]
    allowed_domains: Set[str]

    # SMTP
    # Параметры SMTP
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str

    # DB
    db_url: str

    # Limits / Flow
    otp_ttl_seconds: int
    otp_cooldown_seconds: int
    resend_max_per_session: int
    email_max_attempts: int
    otp_max_attempts: int

    # Misc
    log_level: str
    banned_words: List[str]

    @classmethod
    def load(cls) -> "Settings":
        """
        Загружает конфигурацию из переменных окружения.

        Читает .env файл из корня проекта и инициализирует объект Settings
        со всеми необходимыми параметрами (токен бота, SMTP, БД, лимиты и т.д.).
        Если BOT_TOKEN не задан, вызывает исключение RuntimeError.

        Args:
            None

        Returns:
            Settings: объект с загруженной конфигурацией.

        Raises:
            RuntimeError: если BOT_TOKEN не задан в переменных окружения.
        """
        load_dotenv()

        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError("BOT_TOKEN не задан в .env")

        allowed_domains = set(_parse_list(os.getenv("ALLOWED_DOMAINS", "")))
        email_regex_str = os.getenv(
            "EMAIL_REGEX", r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
        )
        email_regex = re.compile(email_regex_str)

        admin_ids: Set[int] = set()
        for x in _parse_list(os.getenv("ADMIN_IDS", "")):
            try:
                admin_ids.add(int(x))
            except (ValueError, TypeError):
                pass

        admin_chat_id: int | None = None
        chat_id = os.getenv(
            "ADMIN_CHAT_ID", os.getenv("ADMIN_CHAT_ID_NOTIFICATION", "")
        )
        if chat_id:
            try:
                admin_chat_id = int(chat_id)
            except (ValueError, TypeError):
                pass

        smtp_host = os.getenv("SMTP_HOST", "").strip()
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "").strip()
        smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
        smtp_from = os.getenv("SMTP_FROM", smtp_user).strip()

        db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/db.sqlite3")

        otp_ttl_seconds = int(os.getenv("OTP_TTL_SECONDS", "120"))  # 2min
        otp_cooldown_seconds = int(os.getenv("OTP_COOLDOWN_SECONDS", "120"))  # 2min
        resend_max_per_session = int(os.getenv("RESEND_MAX_PER_SESSION", "3"))
        email_max_attempts = int(os.getenv("EMAIL_MAX_ATTEMPTS", "3"))
        otp_max_attempts = int(os.getenv("OTP_MAX_ATTEMPTS", "3"))

        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        banned_words = load_banned_words()

        return cls(
            bot_token=bot_token,
            admin_ids=admin_ids,
            admin_chat_id=admin_chat_id,
            email_regex_str=email_regex_str,
            email_regex=email_regex,
            allowed_domains=allowed_domains,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            smtp_from=smtp_from,
            db_url=db_url,
            otp_ttl_seconds=otp_ttl_seconds,
            otp_cooldown_seconds=otp_cooldown_seconds,
            resend_max_per_session=resend_max_per_session,
            email_max_attempts=email_max_attempts,
            otp_max_attempts=otp_max_attempts,
            log_level=log_level,
            banned_words=banned_words,
        )
