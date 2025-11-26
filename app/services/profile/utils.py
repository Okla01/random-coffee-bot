"""
Утилиты для работы с профилем и фотографиями.

Содержит функции для управления фото пользователя, валидации данных профиля.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List
import re

from aiogram.types import InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.orm import attributes

from app.services.core.text import contains_banned_words

if TYPE_CHECKING:
    from app.database import User


def load_banned_words() -> List[str]:
    """
    Загружает список запрещённых слов из файла.
    
    Читает файл data/banned_words.txt, где каждое слово на отдельной строке.
    Пустые строки и строки, начинающиеся с #, игнорируются.
    
    Returns:
        List[str]: список запрещённых слов.
    """
    banned_words_file = Path("data/banned_words.txt")
    words = []
    
    if banned_words_file.exists():
        try:
            with open(banned_words_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Игнорируем пустые строки и комментарии
                    if line and not line.startswith("#"):
                        words.append(line)
        except Exception:
            # Если не удалось прочитать файл, возвращаем пустой список
            pass
    
    return words


def normalize_interests(
    raw: str, banned_words: Iterable[str]
) -> tuple[List[str] | None, str | None]:
    """
    Нормализует и валидирует список интересов.

    Разбивает строку по разделителям (запятая, точка с запятой, перевод строки),
    тримит каждый элемент, проверяет индивидуальную длину (1–50 символов),
    удаляет дубликаты (без учёта регистра), проверяет на запрещённые слова
    и контролирует суммарную длину (макс. 300 символов) и количество (макс. 30).

    Args:
        raw (str): исходная строка с интересами, разделённые запятыми/другими разделителями.
        banned_words (Iterable[str]): итерируемое собрание запрещённых слов.

    Returns:
        tuple[list[str] | None, str | None]: (список_интересов_или_None, сообщение_об_ошибке_или_None).
                                               При успехе: (список, None), при ошибке: (None, текст_ошибки).
    """
    if not raw.strip():
        return [], None
    parts = re.split(r"[,\n;]+", raw)
    interests = [p.strip() for p in parts if p.strip()]
    if len(interests) > 30:
        return None, "Слишком много значений (макс. 30)."
    for interest in interests:
        if not (1 <= len(interest) <= 50):
            return None, f"Интерес «{interest}» недопустимой длины"
        has_banned, word = contains_banned_words(interest, banned_words)
        if has_banned:
            return None, f"Интерес «{interest}» содержит недопустимое слово"
    # удаляем дубликаты, сохраняя порядок
    seen = set()
    result: List[str] = []
    for it in interests:
        if it.lower() not in seen:
            seen.add(it.lower())
            result.append(it)
    # итоговая длина строк
    if sum(len(x) for x in result) > 300:
        return None, "Суммарная длина интересов превышает 300 символов."
    return result, None


def get_photos_list(user: User) -> list:
    """Получает список фото пользователя из БД."""
    # Импорт здесь не нужен, так как User используется только для типизации
    # благодаря TYPE_CHECKING и from __future__ import annotations
    return user.photos_json.get("photos", []) if user.photos_json else []


def set_photos_list(user: User, photos_list: list) -> None:
    """Устанавливает список фото пользователя в БД."""
    # Импорт здесь не нужен, так как User используется только для типизации
    # благодаря TYPE_CHECKING и from __future__ import annotations
    if photos_list:
        user.photos_json = {"photos": photos_list}
        attributes.flag_modified(user, "photos_json")
    else:
        user.photos_json = None


async def send_photos(
    bot,
    chat_id: int,
    photos_list: list,
) -> None:
    """
    Отправляет список фото (одиночное или альбом).
    
    Автоматически выбирает способ отправки в зависимости от количества фото.
    """
    if not photos_list:
        return

    count = len(photos_list)

    if count == 1:
        await bot.send_photo(chat_id, photos_list[0]["file_id"])
    else:
        media_group = [
            InputMediaPhoto(media=photo_data["file_id"])
            for photo_data in photos_list
        ]
        try:
            await bot.send_media_group(chat_id, media=media_group)
        except TelegramBadRequest as e:
            print("send_media_group error:", repr(e))
            for photo_data in photos_list:
                await bot.send_photo(chat_id, photo_data["file_id"])


def get_photo_count_text(count: int) -> str:
    """Возвращает правильный текст для количества фото."""
    if count == 1:
        return "Добавлено 1 фото"
    elif 2 <= count <= 4:
        return f"Добавлено {count} фото"
    else:
        return f"Добавлено {count} фотографий"


def is_profile_complete(user: User) -> bool:
    """
    Проверяет, заполнен ли профиль полностью.
    
    Профиль считается заполненным, если все обязательные поля заполнены:
    - имя (name)
    - описание (bio)
    - возраст (age)
    - интересы (interests_json)
    - фото (photos_json с непустым списком photos)
    
    Args:
        user (User): объект пользователя.
        
    Returns:
        bool: True если все обязательные поля заполнены, иначе False.
    """
    return bool(
        user.name and
        user.bio and
        user.age and
        user.interests_json and
        user.photos_json and
        user.photos_json.get("photos")
    )