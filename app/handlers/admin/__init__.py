"""
Обработчики административной панели.
"""

from aiogram import Router

from .admin import router as admin_router
from .blocking import router as blocking_router

# Объединяем все роутеры административной панели
router = Router()
router.include_router(admin_router)
router.include_router(blocking_router)

__all__ = ["router"]

