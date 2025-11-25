"""
Пакет административных функций.

Содержит обработчики для управления пользователями, блокировок и логирования.
"""

from .admin import router as commands_router

__all__ = ["commands_router"]
