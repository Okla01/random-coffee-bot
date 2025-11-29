"""
Константы для работы приложения.

Файл содержит константы, которые используются разными модулями приложения.
"""

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
    "min_jaccard": "0.3",
    "cooldown_weeks": "1",
    "match_day": "fri",
    "match_utc_hour": "12",
}

# Популярные часовые пояса для выбора пользователем
# Формат: (IANA timezone, отображаемое название)
TIMEZONES = [
    ("Europe/Moscow", "Москва (UTC+3)"),
    ("Europe/Kiev", "Киев (UTC+2)"),
    ("Europe/Minsk", "Минск (UTC+3)"),
    ("Asia/Yerevan", "Ереван (UTC+4)"),
    ("Asia/Baku", "Баку (UTC+4)"),
    ("Asia/Tbilisi", "Тбилиси (UTC+4)"),
    ("Asia/Almaty", "Алматы (UTC+6)"),
    ("Asia/Tashkent", "Ташкент (UTC+5)"),
    ("Asia/Bishkek", "Бишкек (UTC+6)"),
    ("Asia/Dushanbe", "Душанбе (UTC+5)"),
    ("Asia/Ashgabat", "Ашхабад (UTC+5)"),
    ("Europe/Kaliningrad", "Калининград (UTC+2)"),
    ("Europe/Samara", "Самара (UTC+4)"),
    ("Asia/Yekaterinburg", "Екатеринбург (UTC+5)"),
    ("Asia/Omsk", "Омск (UTC+6)"),
    ("Asia/Krasnoyarsk", "Красноярск (UTC+7)"),
    ("Asia/Irkutsk", "Иркутск (UTC+8)"),
    ("Asia/Yakutsk", "Якутск (UTC+9)"),
    ("Asia/Vladivostok", "Владивосток (UTC+10)"),
    ("Asia/Magadan", "Магадан (UTC+11)"),
    ("Asia/Kamchatka", "Петропавловск-Камчатский (UTC+12)"),
]