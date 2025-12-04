"""
Все FSM-состояния и ключи для данных, сохраняемых в FSMContext приложения.

Централизованное хранение всех состояний FSM и ключей для данных, 
сохраняемых в FSMContext для удобства управления и поддержки.
"""

from enum import Enum
from aiogram.fsm.state import State, StatesGroup


class AdminSettingsStates(StatesGroup):
    """Состояния FSM для редактирования настроек администратора."""

    waiting_min_jaccard = State()
    waiting_repeat_pair_cooldown_weeks = State()
    waiting_match_day = State()
    waiting_match_msk_time = State()
    waiting_response_timeout_time = State()
    waiting_reminder_interval_time = State()


class AdminUsersStates(StatesGroup):
    """Состояния FSM для поиска пользователей в админке."""
    
    waiting_search_telegram_id = State()


class ComplaintStates(StatesGroup):
    """Состояния FSM для обработки жалоб."""

    waiting_warning_text = State()


class FSMDataKeys(str, Enum):
    """Ключи для данных, сохраняемых в FSMContext."""

    LAST_KB_MID = "last_kb_mid"
    DRAFT_SETTINGS = "draft_settings"
    ADMIN_PANEL_ACTIVE = "admin_panel_active"
    EDITING_FIELD = "editing_field"

    # Ключи для обработки жалоб
    COMPLAINT_ID = "complaint_id"
    COMPLAINT_ADMIN_MESSAGE_ID = "complaint_admin_message_id"
    COMPLAINT_REPORTED_USER_ID = "complaint_reported_user_id"
    COMPLAINT_CANCEL_MESSAGE_ID = "complaint_cancel_message_id"

