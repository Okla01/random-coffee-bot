"""
Пакет авторизации и безопасности.

Политика импортов:
- В коде проекта используем абсолютные импорты `from app.auth import ...`.
- Внутри `__init__.py` применяются относительные импорты для ре-экспорта
	конкретных функций/классов (это общепринятый шаблон).
"""

from .email import send_otp_email
from .security import generate_otp, validate_email
from .registration import router as registration_router

__all__ = [
    "generate_otp",
    "validate_email",
    "send_otp_email",
    "registration_router",
]