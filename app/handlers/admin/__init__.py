"""
Обработчики административной панели.

Объединяет все роутеры для работы административной панели: открытие панели, управление
пользователями (блокировка, назначение ролей, просмотр карточек), обработка жалоб,
управление настройками системы, просмотр статистики, экспорт данных, inline-поиск
пользователей и одобрение заявок на доступ к анкете. Порядок подключения роутеров важен:
более специфичные обработчики должны быть выше в списке.
"""

from aiogram import Router

from .admin import router as admin_router
from .blocking import router as blocking_router
from .settings import router as settings_router
from .exit import router as exit_router
from .users import router as users_router
from .inline_search import router as inline_search_router
from .user_card import router as user_card_router
from .name_approval import router as name_approval_router
from .complaints import router as complaints_router
from .statistics import router as statistics_router

# Объединяем все роутеры административной панели
# Порядок важен: более специфичные обработчики должны быть выше
router = Router()
router.include_router(admin_router)
router.include_router(complaints_router)  # Обработчики жалоб
router.include_router(settings_router)  # Специфичные callback-обработчики настроек
router.include_router(blocking_router)  # Обработчики блокировки (из уведомлений)
router.include_router(
    name_approval_router
)  # Обработчики одобрения заявок на доступ к анкете
router.include_router(
    user_card_router
)  # Обработчики карточки пользователя (из inline-поиска)
router.include_router(exit_router)  # Обработчик выхода из админ-панели
router.include_router(users_router)  # Обработчик списка пользователей
router.include_router(inline_search_router)  # Inline-поиск пользователей
router.include_router(statistics_router)  # Обработчик статистики

__all__ = ["router"]
