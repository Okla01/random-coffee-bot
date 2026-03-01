"""
Буфер медиа-групп (альбомов) для обработки пакетной загрузки фото.

Telegram отправляет альбом как несколько отдельных сообщений с одинаковым
media_group_id. Этот модуль реализует буфер для накопления кадров альбома
и координации их обработки.
"""

from __future__ import annotations

import asyncio
from aiogram.types import PhotoSize


# {media_group_id: [PhotoSize, ...]}
_media_group_buffer: dict[str, list[PhotoSize]] = {}

# {media_group_id: asyncio.Task}
_media_group_tasks: dict[str, asyncio.Task] = {}


def add_to_media_group_buffer(
    media_group_id: str, photo: PhotoSize
) -> list[PhotoSize]:
    """
    Сохраняет кадр альбома в буфер.

    Args:
        media_group_id: идентификатор медиа-группы.
        photo: объект фото.

    Returns:
        list[PhotoSize]: все фото для этой медиа-группы.
    """
    media_group_id = str(media_group_id)
    group = _media_group_buffer.setdefault(media_group_id, [])
    group.append(photo)
    return group


def get_and_clear_media_group_buffer(
    media_group_id: str,
) -> list[PhotoSize]:
    """
    Забирает все кадры альбома и очищает буфер.

    Args:
        media_group_id: идентификатор медиа-группы.

    Returns:
        list[PhotoSize]: список всех фото из буфера.
    """
    return _media_group_buffer.pop(str(media_group_id), [])


def is_media_group_processing(media_group_id: str) -> bool:
    """Проверяет, обрабатывается ли уже эта медиа-группа."""
    return str(media_group_id) in _media_group_tasks


def set_media_group_task(media_group_id: str, task: asyncio.Task) -> None:
    """Сохраняет задачу обработки медиа-группы."""
    _media_group_tasks[str(media_group_id)] = task


def remove_media_group_task(media_group_id: str) -> None:
    """Удаляет задачу обработки медиа-группы."""
    _media_group_tasks.pop(str(media_group_id), None)
