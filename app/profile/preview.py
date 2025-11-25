"""
Утилиты для формирования и отправки текстового предпросмотра профиля.

Содержит функции для построения текстового представления анкеты пользователя,
отправки фотографий профиля и сообщения с кнопками.
"""

from aiogram.types import InputMediaPhoto


async def _send_profile_preview(bot, chat_id: int, user, state, reply_markup, send_photos: bool = True, send_preview_text: bool = True) -> None:
    """
    Отправляет фото профиля (если есть) и текстовый предпросмотр профиля с клавиатурой.

    Сначала отправляет медиа-группу с фотографиями пользователя (если добавлены),
    затем формирует текстовое представление анкеты и отправляет сообщение с клавиатурой,
    сохраняя ID сообщения в FSM-состояние для последующего гашения кнопок.

    Args:
        bot: объект бота/клиента aiogram.
        chat_id (int): идентификатор чата для отправки.
        user: объект пользователя (модель из БД).
        state: FSMContext для сохранения `last_kb_mid`.
        reply_markup: клавиатура для сообщения.
        send_photos (bool): отправлять ли фотографии. По умолчанию True.
        send_preview_text (bool): отправлять ли текстовый предпросмотр. По умолчанию True.

    Returns:
        None: ничего не возвращает.
    """
    # Отправляем фото если они есть и send_photos=True
    if send_photos and user.photos_json and user.photos_json.get("photos"):
        photos_list = user.photos_json.get("photos", [])
        if photos_list:
            # Строим медиа-группу
            media_group = []
            for idx, photo_data in enumerate(photos_list):
                caption = f"Добавлено {len(photos_list)} фото" if idx == 0 else None
                media_group.append(
                    InputMediaPhoto(media=photo_data["file_id"], caption=caption)
                )

            # Отправляем медиа-группу
            try:
                await bot.send_media_group(chat_id, media=media_group)
            except Exception:
                # Если не удалось отправить группу, отправляем по одному
                for idx, photo_data in enumerate(photos_list):
                    caption = f"Добавлено {len(photos_list)} фото" if idx == 0 else None
                    await bot.send_photo(chat_id, photo_data["file_id"], caption=caption)

    # Отправляем текстовый предпросмотр если send_preview_text=True
    if send_preview_text:
        preview = build_profile_preview_text(user)
        sent = await bot.send_message(chat_id, preview, reply_markup=reply_markup)
        await state.update_data(last_kb_mid=sent.message_id)
    elif reply_markup:
        # Если только клавиатура без текста предпросмотра
        sent = await bot.send_message(chat_id, " ", reply_markup=reply_markup)
        await state.update_data(last_kb_mid=sent.message_id)


def build_profile_preview_text(user) -> str:
    """
    Формирует текстовое представление профиля пользователя.

    Собирает все данные профиля (имя, возраст, описание, интересы) в форматированный
    многострочный текст с эмодзи-иконками для каждого поля.

    Args:
        user: объект пользователя (модель из БД).

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
