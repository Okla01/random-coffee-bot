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
    waiting_cooldown_weeks = State()
    waiting_match_day = State()
    waiting_match_utc_hour = State()


class FSMDataKeys(str, Enum):
    """Ключи для данных, сохраняемых в FSMContext."""

    LAST_KB_MID = "last_kb_mid"
    DRAFT_SETTINGS = "draft_settings"
    ADMIN_PANEL_ACTIVE = "admin_panel_active"
    EDITING_FIELD = "editing_field"

