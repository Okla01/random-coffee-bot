"""
Роутеры для callback-логики матчинга.
"""

from aiogram import Router

from .responses import router as responses_router
from .slots import router as slots_router

router = Router()
router.include_router(responses_router)
router.include_router(slots_router)

__all__ = ["router"]

