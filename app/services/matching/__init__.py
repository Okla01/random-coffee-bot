"""
Пакет с утилитами и сервисами мэтчинга Random Coffee.
"""

from .constants import (
    MATCH_ACTIVE_STATUSES,
    MATCH_INACTIVE_STATUSES,
    MATCH_STATUS_COMPLETED,
    MATCH_STATUS_EXPIRED_TIMEOUT,
    MATCH_STATUS_PENDING_RESPONSE,
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_SKIPPED,
    MATCH_STATUS_USER_A_BLOCKED,
    MATCH_STATUS_USER_B_BLOCKED,
    MATCH_USER_RESPONSE_CONFIRM,
    MATCH_USER_RESPONSE_SKIP,
    MATCH_SUCCESS_STATUSES,
)
from .settings import (
    MatchingSettings,
    get_setting_bool,
    get_setting_float,
    get_setting_int,
    load_matching_settings,
)
from .utils import compute_jaccard, extract_interests_list, user_has_active_match
from .round import run_matching_round
from .jobs import process_match_timeouts_and_reminders
from .scheduler import setup_matching_scheduler

__all__ = [
    # settings helpers
    "MatchingSettings",
    "get_setting_bool",
    "get_setting_float",
    "get_setting_int",
    "load_matching_settings",
    # match statuses/responses
    "MATCH_STATUS_PENDING_RESPONSE",
    "MATCH_STATUS_SKIPPED",
    "MATCH_STATUS_MATCHED",
    "MATCH_STATUS_COMPLETED",
    "MATCH_STATUS_USER_A_BLOCKED",
    "MATCH_STATUS_USER_B_BLOCKED",
    "MATCH_STATUS_EXPIRED_TIMEOUT",
    "MATCH_ACTIVE_STATUSES",
    "MATCH_INACTIVE_STATUSES",
    "MATCH_SUCCESS_STATUSES",
    "MATCH_USER_RESPONSE_SKIP",
    "MATCH_USER_RESPONSE_CONFIRM",
    # utilities
    "compute_jaccard",
    "extract_interests_list",
    "user_has_active_match",
    "run_matching_round",
    "process_match_timeouts_and_reminders",
    "setup_matching_scheduler",
]
