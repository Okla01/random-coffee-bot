"""
Обработчики административной панели.
"""

from aiogram import Router

from .admin import router as admin_router
from .blocking import router as blocking_router
from .settings import router as settings_router

# Объединяем все роутеры административной панели
# Порядок важен: более специфичные обработчики должны быть выше
router = Router()
router.include_router(admin_router)
router.include_router(settings_router)  # Специфичные callback-обработчики настроек
router.include_router(blocking_router)  # Обработчики блокировки

__all__ = ["router"]

