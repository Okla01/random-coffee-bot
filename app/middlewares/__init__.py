"""
Пакет промежуточных обработчиков (middlewares) для Aiogram.

Политика импортов:
- Внутри проекта используем абсолютные импорты `from app.middlewares import ...`.
- `__init__.py` ре-экспортирует middleware классы из внутренних модулей
	с помощью относительных импортов (напр., `from .db_session import ...`).
"""

from .db_session import DbSessionMiddleware
from .blocked_user import BlockedUserMiddleware
from .scheduler import SchedulerMiddleware

__all__ = ["DbSessionMiddleware", "BlockedUserMiddleware", "SchedulerMiddleware"]