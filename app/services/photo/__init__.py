"""
Пакет централизованной работы с фотографиями.

Вся бизнес-логика фото сосредоточена здесь:
- service.py — основной сервис (хранение, отправка, обновление file_id)
- buffer.py  — буфер медиа-групп (альбомов)
"""

from app.services.photo.service import (
    MAX_PHOTOS,
    get_photos_data,
    set_photos_data,
    get_photo_count,
    has_photos,
    can_add_photo,
    upload_to_storage,
    refresh_file_id,
    build_user_media_group,
    send_user_photos,
    add_single_photo,
    add_photos,
    get_telegram_profile_photo,
    add_telegram_profile_photo,
    clear_user_photos,
    send_photo_request,
)

from app.services.photo.buffer import (
    add_to_media_group_buffer,
    get_and_clear_media_group_buffer,
    is_media_group_processing,
    set_media_group_task,
    remove_media_group_task,
)

__all__ = [
    "MAX_PHOTOS",
    "get_photos_data",
    "set_photos_data",
    "get_photo_count",
    "has_photos",
    "can_add_photo",
    "upload_to_storage",
    "refresh_file_id",
    "build_user_media_group",
    "send_user_photos",
    "add_single_photo",
    "add_photos",
    "get_telegram_profile_photo",
    "add_telegram_profile_photo",
    "clear_user_photos",
    "send_photo_request",
    "add_to_media_group_buffer",
    "get_and_clear_media_group_buffer",
    "is_media_group_processing",
    "set_media_group_task",
    "remove_media_group_task",
]
