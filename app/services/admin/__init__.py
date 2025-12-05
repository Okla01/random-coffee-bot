"""
Бизнес-логика административных функций.

Экспортирует функции для работы с ролями, проверки прав и блокировки пользователей.
"""

from .roles import sync_admin_role, is_admin, grant_admin_role, revoke_admin_role
from .blocking import block_user, unblock_user
from .admin import process_admin_command, AdminAccessResultType
from .complaints import submit_complaint

__all__ = [
    "sync_admin_role",
    "is_admin",
    "grant_admin_role",
    "revoke_admin_role",
    "block_user",
    "unblock_user",
    "process_admin_command",
    "AdminAccessResultType",
    "submit_complaint",
]
