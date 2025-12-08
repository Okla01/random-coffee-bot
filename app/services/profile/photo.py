"""
Бизнес-логика работы с фотографиями профиля.

Содержит функции для:
- управления буфером медиа-групп
- добавления фото в профиль
- проверки лимитов
- получения фото из профиля Telegram
- очистки фото
"""

from __future__ import annotations

import asyncio
from typing import Optional

from aiogram.fsm.context import FSMContext
from aiogram.types import PhotoSize

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.database import User
from app.database.utils import now_msk
from aiogram.types import InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from app.handlers.fsm import FSMDataKeys


# Максимальное количество фото в профиле
MAX_PHOTOS = 5

# ----------------- Буфер медиа-групп (альбомов) ------------------ #

# {media_group_id: [PhotoSize, ...]}
_media_group_buffer: dict[str, list[PhotoSize]] = {}
# {media_group_id: asyncio.Task} – чтобы не запускать обработку альбома несколько раз
_media_group_tasks: dict[str, asyncio.Task] = {}


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
    from app.keyboards.kb_profile import kb_profile_photo as default_kb

    if kb is None:
        kb = default_kb()

    # Для CallbackQuery используем message.answer(), для Message просто answer()
    if hasattr(message_or_cq, "message"):
        # Это CallbackQuery
        sent = await message_or_cq.message.answer(
            "Добавь свое фото",
            reply_markup=kb,
        )
    else:
        # Это Message
        sent = await message_or_cq.answer(
            "Добавь свое фото",
            reply_markup=kb,
        )

    if sent and hasattr(sent, "message_id"):
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})


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
            InputMediaPhoto(media=photo_data["file_id"]) for photo_data in photos_list
        ]
        try:
            await bot.send_media_group(chat_id, media=media_group)
        except TelegramBadRequest as e:
            print("send_media_group error:", repr(e))
            for photo_data in photos_list:
                await bot.send_photo(chat_id, photo_data["file_id"])


def add_to_media_group_buffer(media_group_id: str, photo: PhotoSize) -> list[PhotoSize]:
    """
    Сохраняет кадр альбома во временный буфер и возвращает весь список кадров
    для этой media_group.

    Args:
        media_group_id (str): идентификатор медиа-группы.
        photo (PhotoSize): объект фото для добавления.

    Returns:
        list[PhotoSize]: весь список фото для этой медиа-группы.
    """
    media_group_id = str(media_group_id)
    group = _media_group_buffer.setdefault(media_group_id, [])
    group.append(photo)
    return group


def get_and_clear_media_group_buffer(media_group_id: str) -> list[PhotoSize]:
    """
    Забирает все кадры альбома и очищает буфер.

    Args:
        media_group_id (str): идентификатор медиа-группы.

    Returns:
        list[PhotoSize]: список всех фото из буфера для этой медиа-группы.
    """
    media_group_id = str(media_group_id)
    return _media_group_buffer.pop(media_group_id, [])


def is_media_group_processing(media_group_id: str) -> bool:
    """
    Проверяет, обрабатывается ли уже эта медиа-группа.

    Args:
        media_group_id (str): идентификатор медиа-группы.

    Returns:
        bool: True если обработка уже запущена, иначе False.
    """
    return str(media_group_id) in _media_group_tasks


def set_media_group_task(media_group_id: str, task: asyncio.Task) -> None:
    """
    Сохраняет задачу обработки медиа-группы.

    Args:
        media_group_id (str): идентификатор медиа-группы.
        task (asyncio.Task): задача обработки.
    """
    _media_group_tasks[str(media_group_id)] = task


def remove_media_group_task(media_group_id: str) -> None:
    """
    Удаляет задачу обработки медиа-группы.

    Args:
        media_group_id (str): идентификатор медиа-группы.
    """
    _media_group_tasks.pop(str(media_group_id), None)


async def add_photos_to_profile(
    session: AsyncSession,
    user: User,
    photos: list[PhotoSize],
) -> tuple[bool, list]:
    """
    Добавляет фото в профиль пользователя с учётом лимита.

    Args:
        session (AsyncSession): сессия БД.
        user (User): объект пользователя.
        photos (list[PhotoSize]): список фото для добавления.

    Returns:
        tuple[bool, list]: (успешно_добавлено, список_всех_фото).
                          Если успешно - (True, обновлённый_список),
                          если лимит превышен - (False, текущий_список).
    """
    photos_list = get_photos_list(user)
    free_slots = MAX_PHOTOS - len(photos_list)

    if free_slots <= 0:
        return False, photos_list

    # Добавляем столько фото, сколько помещается
    for photo in photos[:free_slots]:
        photos_list.append(
            {
                "file_id": photo.file_id,
                "ts": now_msk().isoformat(),
            }
        )

    set_photos_list(user, photos_list)
    await session.commit()
    return True, photos_list


async def add_single_photo_to_profile(
    session: AsyncSession,
    user: User,
    photo: PhotoSize,
) -> tuple[bool, list]:
    """
    Добавляет одно фото в профиль пользователя с учётом лимита.

    Args:
        session (AsyncSession): сессия БД.
        user (User): объект пользователя.
        photo (PhotoSize): фото для добавления.

    Returns:
        tuple[bool, list]: (успешно_добавлено, список_всех_фото).
                          Если успешно - (True, обновлённый_список),
                          если лимит превышен - (False, текущий_список).
    """
    photos_list = get_photos_list(user)

    if len(photos_list) >= MAX_PHOTOS:
        return False, photos_list

    photos_list.append(
        {
            "file_id": photo.file_id,
            "ts": now_msk().isoformat(),
        }
    )

    set_photos_list(user, photos_list)
    await session.commit()
    return True, photos_list


async def get_telegram_profile_photo(
    bot,
    user_id: int,
) -> Optional[PhotoSize]:
    """
    Получает фото профиля пользователя из Telegram.

    Args:
        bot: объект бота aiogram.
        user_id (int): Telegram ID пользователя.

    Returns:
        Optional[PhotoSize]: объект фото или None, если фото нет.
    """
    try:
        user_photos = await bot.get_user_profile_photos(user_id, limit=1)
        if user_photos.photos:
            return user_photos.photos[0][-1]
        return None
    except Exception:
        return None


async def add_telegram_profile_photo(
    session: AsyncSession,
    user: User,
    photo: PhotoSize,
) -> tuple[bool, list]:
    """
    Добавляет фото из профиля Telegram в профиль пользователя.

    Args:
        session (AsyncSession): сессия БД.
        user (User): объект пользователя.
        photo (PhotoSize): фото из профиля Telegram.

    Returns:
        tuple[bool, list]: (успешно_добавлено, список_всех_фото).
    """
    photos_list = get_photos_list(user)

    if len(photos_list) >= MAX_PHOTOS:
        return False, photos_list

    photos_list.append(
        {
            "file_id": photo.file_id,
            "ts": now_msk().isoformat(),
        }
    )

    set_photos_list(user, photos_list)
    await session.commit()
    return True, photos_list


async def clear_user_photos(
    session: AsyncSession,
    user: User,
) -> None:
    """
    Очищает все фото пользователя.

    Args:
        session (AsyncSession): сессия БД.
        user (User): объект пользователя.
    """
    user.photos_json = None
    user.stage = "profile_photo"
    await session.commit()


def can_add_photo(user: User) -> bool:
    """
    Проверяет, можно ли добавить ещё фото.

    Args:
        user (User): объект пользователя.

    Returns:
        bool: True если можно добавить, иначе False.
    """
    photos_list = get_photos_list(user)
    return len(photos_list) < MAX_PHOTOS


def get_photo_count(user: User) -> int:
    """
    Возвращает количество фото в профиле пользователя.

    Args:
        user (User): объект пользователя.

    Returns:
        int: количество фото.
    """
    photos_list = get_photos_list(user)
    return len(photos_list)


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
