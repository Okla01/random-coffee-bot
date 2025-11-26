"""
Роутеры обработчиков команд и сообщений.
"""

from .start import router as start_router
from .admin import router as admin_router

__all__ = ["start_router", "admin_router"]
