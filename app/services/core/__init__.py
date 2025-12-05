"""
Пакет ядра приложения (бизнес-логика).

Политика импортов в проекте:
- Внутри модулей проекта используем преимущественно абсолютные импорты
        вида `from app.package import ...` — это делает импорты однозначными
        и независимыми от текущей рабочей директории.
- В `__init__` файлах пакетов допускается использование относительных
        импортов (например, `from .config import Settings`) исключительно для
        ре-экспорта публичного API пакета. Это удобный и общепринятый паттерн.

Примеры:
                from app.services.core import Settings
                # внутри __init__.py: from .config import Settings

"""

from .config import Settings
from .logger import setup_logging

__all__ = ["Settings", "setup_logging"]
