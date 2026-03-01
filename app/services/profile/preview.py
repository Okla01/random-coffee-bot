"""
Утилиты для формирования и отправки текстового предпросмотра профиля (бизнес-логика).

Содержит функции для построения текстового представления анкеты пользователя,
отправки фотографий профиля и сообщения с кнопками.
"""

from app.database import User
from app.handlers.fsm import FSMDataKeys


async def send_profile_preview(
    bot,
    chat_id: int,
    user: User,
    state,
    reply_markup,
    settings=None,
    send_photos: bool = True,
    send_preview_text: bool = True,
) -> None:
    """
    Отправляет фото профиля (если есть) и текстовый предпросмотр профиля с клавиатурой.

    Args:
        bot: объект бота/клиента aiogram.
        chat_id (int): идентификатор чата для отправки.
        user (User): объект пользователя (модель из БД).
        state: FSMContext для сохранения `last_kb_mid`.
        reply_markup: клавиатура для сообщения.
        settings: настройки приложения (Settings). Нужны для отправки фото.
        send_photos (bool): отправлять ли фотографии. По умолчанию True.
        send_preview_text (bool): отправлять ли текстовый предпросмотр. По умолчанию True.

    Returns:
        None: ничего не возвращает.
    """
    # Отправляем фото если они есть и send_photos=True
    if send_photos and settings:
        from app.services.photo import send_user_photos, get_photo_count, has_photos as _has_photos

        if _has_photos(user):
            count = get_photo_count(user)
            caption = f"Добавлено {count} фото"
            await send_user_photos(bot, chat_id, user, settings, caption=caption)

    # Отправляем текстовый предпросмотр если send_preview_text=True
    if send_preview_text:
        preview = build_profile_preview_text(user)
        sent = await bot.send_message(chat_id, preview, reply_markup=reply_markup)
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
    elif reply_markup:
        # Если только клавиатура без текста предпросмотра
        sent = await bot.send_message(chat_id, " ", reply_markup=reply_markup)
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})


def build_profile_preview_text(user: User) -> str:
    """
    Формирует текстовое представление профиля пользователя.

    Args:
        user (User): объект пользователя (модель из БД).

    Returns:
        str: многострочный текст предпросмотра.
    """
    lines = ["📇 Предпросмотр анкеты:"]
    if getattr(user, "name", None):
        lines.append(f"• Имя: {user.name}")
    if getattr(user, "age", None):
        lines.append(f"• Возраст: {user.age}")
    if getattr(user, "bio", None):
        lines.append(f"• О себе: {user.bio}")
    interests = (getattr(user, "interests_json", None) or {}).get("interests", [])
    if interests:
        lines.append("• Интересы: " + ", ".join(interests))
    return "\n".join(lines)
