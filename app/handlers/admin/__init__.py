"""
Обработчики административной панели.
"""

from aiogram import Router

from .admin import router as admin_router
from .blocking import router as blocking_router
from .settings import router as settings_router
from .exit import router as exit_router
from .users import router as users_router
from .inline_search import router as inline_search_router

# Объединяем все роутеры административной панели
# Порядок важен: более специфичные обработчики должны быть выше
router = Router()
router.include_router(admin_router)
router.include_router(settings_router)  # Специфичные callback-обработчики настроек
router.include_router(blocking_router)  # Обработчики блокировки
router.include_router(exit_router)  # Обработчик выхода из админ-панели
router.include_router(users_router)  # Обработчик списка пользователей
router.include_router(inline_search_router)  # Inline-поиск пользователей

__all__ = ["router"]

