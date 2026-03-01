"""
Обратная совместимость: реэкспорт из app.services.photo.

Все функции перенесены в app/services/photo/.
Этот файл оставлен для обратной совместимости импортов.
"""

from app.services.photo import (  # noqa: F401
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
    add_to_media_group_buffer,
    get_and_clear_media_group_buffer,
    is_media_group_processing,
    set_media_group_task,
    remove_media_group_task,
)

# Алиасы для старых имён (обратная совместимость)
get_photos_list = get_photos_data
set_photos_list = set_photos_data
add_photos_to_profile = add_photos
add_single_photo_to_profile = add_single_photo
