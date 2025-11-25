"""
Функции безопасности и валидации для авторизации.

Содержит функции для криптографически стойкой генерации OTP,
валидации адресов электронной почты с проверкой домена и других операций безопасности.
"""

from __future__ import annotations

import secrets
import re


def generate_otp(length: int = 6) -> str:
    """
    Генерирует криптографически стойкий одноразовый пароль.

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