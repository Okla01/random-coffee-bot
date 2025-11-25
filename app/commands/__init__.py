"""
Пакет команд бота.

Содержит обработчики для команд /start и других глобальных команд.
"""

from .start import router as start_router

__all__ = ["start_router"]
