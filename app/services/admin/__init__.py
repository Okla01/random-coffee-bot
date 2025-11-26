"""
Бизнес-логика административных функций.

Экспортирует функции для работы с ролями, проверки прав и блокировки пользователей.
"""

from .roles import sync_admin_role, is_admin
from .blocking import block_user, unblock_user
from .admin import process_admin_command, AdminAccessResultType

__all__ = [
    "sync_admin_role",
    "is_admin",
    "block_user",
    "unblock_user",
    "process_admin_command",
    "AdminAccessResultType",
]

