"""
Роутеры для callback-логики мэтчинга.
"""

from aiogram import Router

from .responses import router as responses_router

router = Router()
router.include_router(responses_router)

__all__ = ["router"]
