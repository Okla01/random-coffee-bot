"""
Утилиты для работы с цензурой текста и отправкой текстовых сообщений.
"""

from __future__ import annotations

from typing import Iterable

from aiogram.fsm.context import FSMContext


def contains_banned_words(
    text: str, banned_words: Iterable[str]
) -> tuple[bool, str | None]:
    """
    Проверяет наличие запрещённых слов в тексте.

    Выполняет поиск слов из списка banned_words в тексте без учёта регистра.
    Возвращает результат в виде кортежа с флагом наличия и самим словом.

    Args:
        text (str): текст для проверки.
        banned_words (Iterable[str]): итерируемое собрание запрещённых слов.

    Returns:
        tuple[bool, str | None]: (найдено_ли_запрещённое_слово, само_слово_или_None).
    """
    low = text.lower()
    for w in banned_words:
        w = w.strip().lower()
        if w and w in low:
            return True, w
    return False, None


async def send_photo_request(
    message_or_cq,
    state: FSMContext,
    kb=None,
) -> None:
    """
    Отправляет стандартный запрос на загрузку фото с клавиатурой.
    
    Args:
        message_or_cq: Message или CallbackQuery объект
        state (FSMContext): контекст FSM
        kb: клавиатура (по умолчанию kb_profile_photo())
    """
    # Импортируем здесь, чтобы избежать циклических импортов
    from app.profile.keyboards import kb_profile_photo as default_kb
    
    if kb is None:
        kb = default_kb()
    
    # Для CallbackQuery используем message.answer(), для Message просто answer()
    if hasattr(message_or_cq, 'message'):
        # Это CallbackQuery
        sent = await message_or_cq.message.answer(
            "Пришлите пожалуйста фото для анкеты (от 1 до 3 фото), "
            "либо используйте текущее фото вашего профиля.",
            reply_markup=kb,
        )
    else:
        # Это Message
        sent = await message_or_cq.answer(
            "Пришлите пожалуйста фото для анкеты (от 1 до 3 фото), "
            "либо используйте текущее фото вашего профиля.",
            reply_markup=kb,
        )
    
    if sent and hasattr(sent, 'message_id'):
        await state.update_data(last_kb_mid=sent.message_id)