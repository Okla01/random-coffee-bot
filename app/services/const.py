"""
Константы для работы приложения.

Файл содержит константы, которые используются разными модулями приложения.
"""

# Дни недели для встреч
DAYS_OF_WEEK = {
    "mon": "Понедельник",
    "tue": "Вторник",
    "wed": "Среда",
    "thu": "Четверг",
    "fri": "Пятница",
    "sat": "Суббота",
    "sun": "Воскресенье",
}

# Дефолтные настройки, которые будут созданы при инициализации
DEFAULT_SETTINGS: dict[str, str] = {
    "matching_enabled": "true",
    "min_jaccard": "0.3",
    "repeat_pair_cooldown_weeks": "1",
    "match_day": "fri",
    "match_msk_time": "12:00",
    "response_timeout_time": "8:00",
    "reminder_interval_time": "1:00",
    "feedback_day": "sun",
    "feedback_msk_time": "18:00",
}

# Названия ролей в БД (ключи)
ROLE_ADMIN = "admin"

# Расшифровка названий ролей для читаемости
ROLE_NAMES: dict[str, str] = {
    ROLE_ADMIN: "Администратор",
}

# Количество пользователей на странице в административной панели
USERS_PER_PAGE = 10

# Статусы пользователей
USER_STATUS_NEW = "new"
USER_STATUS_ACTIVE = "active"
USER_STATUS_BLOCKED = "blocked"
USER_STATUS_NOT_ACTIVE = "not_active"

# Расшифровка статусов пользователей для читаемости
USER_STATUS_NAMES: dict[str, str] = {
    USER_STATUS_NEW: "Новый",
    USER_STATUS_ACTIVE: "Активный",
    USER_STATUS_BLOCKED: "Заблокирован",
    USER_STATUS_NOT_ACTIVE: "Не активен",
}

# Константы для inline-поиска

# Ключи для словаря результата inline-поиска
IS_RESULT_KEY_ID = "id"
IS_RESULT_KEY_TITLE = "title"
IS_RESULT_KEY_DESCRIPTION = "description"
IS_RESULT_KEY_MESSAGE_TEXT = "message_text"
IS_RESULT_KEY_HAS_PHOTOS = "has_photos"

# Ключи для словаря данных профиля пользователя
UPD_KEY_PROFILE_TEXT = "profile_text"
UPD_KEY_PHOTOS_LIST = "photos_list"
UPD_KEY_HAS_PHOTOS = "has_photos"

# Статусы жалоб
COMPLAINT_STATUS_PENDING = "pending"  # Ожидает рассмотрения
COMPLAINT_STATUS_CLOSED = "closed"  # Закрыта без санкций
COMPLAINT_STATUS_WARNED = "warned"  # Выдано предупреждение
COMPLAINT_STATUS_BLOCKED = "blocked"  # Пользователь заблокирован

COMPLAINT_STATUS_NAMES: dict[str, str] = {
    COMPLAINT_STATUS_PENDING: "Ожидает",
    COMPLAINT_STATUS_CLOSED: "Закрыто",
    COMPLAINT_STATUS_WARNED: "Предупреждение",
    COMPLAINT_STATUS_BLOCKED: "Заблокирован",
}
