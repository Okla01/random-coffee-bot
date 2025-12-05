"""
Обработчики авторизации и регистрации.
"""

from .registration import router as registration_router

__all__ = ["registration_router"]
