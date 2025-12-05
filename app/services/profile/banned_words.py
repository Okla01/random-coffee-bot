"""
Логика для работы с запрещёнными словами.

Содержит функции для загрузки списка запрещённых слов из файла.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List
import re


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


def contains_banned_words(
    text: str, banned_words: Iterable[str]
) -> tuple[bool, str | None]:
    """
    Проверяет наличие запрещённых слов в тексте.

    Выполняет поиск слов из списка banned_words в тексте без учёта регистра.
    Слова проверяются как целые слова (не подстроки), например:
    - "спам" найдется в "Данил Спам" или "спам реклама"
    - "спам" НЕ найдется в "ДанСПАМил" или "спамм"

    Args:
        text (str): текст для проверки.
        banned_words (Iterable[str]): итерируемое собрание запрещённых слов.

    Returns:
        tuple[bool, str | None]: (найдено_ли_запрещённое_слово, само_слово_или_None).
    """
    low = text.lower()
    for w in banned_words:
        w = w.strip().lower()
        if not w:
            continue

        # Используем регулярное выражение с границами слов (\b)
        # Это гарантирует, что слово ищется как целое, а не как подстрока
        pattern = r"\b" + re.escape(w) + r"\b"

        if re.search(pattern, low):
            return True, w
    return False, None
