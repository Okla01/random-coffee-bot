"""
Пакет `database` — реализация базы данных и моделей.

Политика импортов:
- Модули проекта используют абсолютные импорты `from app.package import ...`.
- `__init__.py` ре-экспортирует удобный публичный API пакета. Для ре-экспорта
	внутри `__init__.py` допустимы относительные импорты (`from .db import ...`).

Это позволяет библиотекам и коду в проекте писать:
		from app.database import User
не вдаваясь в внутреннюю структуру `app.database.models`.
"""

from .db import make_engine, make_session_factory, lifespan_db
from .models import *

__all__ = [
    "make_engine",
    "make_session_factory",
    "lifespan_db",
    "Base",
    "User",
    "Otp",
    "AuthAttempt",
    "Role",
    "UserRole",
    "AdminLog",
    "Match",
    "Complaint",
    "Setting",
]
