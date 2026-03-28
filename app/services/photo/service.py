"""
Централизованный сервис для работы с фотографиями.

Все операции с фото пользователей:
- чтение/запись данных фото в БД
- загрузка в чат-хранилище (upload)
- отправка пользователю (send)
- обновление file_id через message_id (refresh)
- добавление/удаление фото из профиля

Формат хранения в photos_json:
  {"photos": [{"message_id": 123, "file_id": "AgAC..."}, ...]}
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaPhoto, PhotoSize
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

if TYPE_CHECKING:
    from aiogram import Bot
    from app.services.core.config import Settings

from app.database import User

logger = logging.getLogger(__name__)

# Максимальное количество фото в профиле
MAX_PHOTOS = 5

PHOTO_CHECK_OK = "ok"
PHOTO_CHECK_REFRESHED = "refreshed"
PHOTO_CHECK_BROKE_FILE_ID = "broke_fileId"
PHOTO_CHECK_MESSAGE_MISSING = "message_missing"
PHOTO_CHECK_TRANSIENT_ERROR = "transient_error"
PHOTO_CHECK_BROKEN_RECORD = "broken_record"

PHOTO_LOG_PATH = Path(__file__).resolve().parents[3] / "data" / "log_photo.txt"

_BAD_FILE_ERROR_MARKERS = (
    "wrong file",
    "file_reference",
    "can't unserialize",
    "wrong remote file identifier",
)
_MISSING_MESSAGE_ERROR_MARKERS = (
    "message not found",
    "to forward not found",
    "message to copy not found",
)


def _is_bad_file_error(error: Exception) -> bool:
    error_msg = str(error).lower()
    return any(marker in error_msg for marker in _BAD_FILE_ERROR_MARKERS)


def _is_message_missing_error(error: Exception) -> bool:
    error_msg = str(error).lower()
    return "message" in error_msg and any(
        marker in error_msg for marker in _MISSING_MESSAGE_ERROR_MARKERS
    )


def _append_photo_issue_log(
    user: User,
    photo_entry: dict,
    reason: str,
    details: str | None = None,
) -> None:
    try:
        PHOTO_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = (
            f"[{timestamp}] user_id={getattr(user, 'id', None)} "
            f"telegram_id={getattr(user, 'telegram_id', None)} "
            f"message_id={photo_entry.get('message_id')} "
            f"file_id={photo_entry.get('file_id')} "
            f"reason={reason}"
        )
        if details:
            payload += f" details={details}"
        with PHOTO_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(payload + "\n")
    except Exception as exc:
        logger.error("Failed to write photo issue to %s: %s", PHOTO_LOG_PATH, exc)


async def _persist_photo_changes(
    user: User,
    settings: "Settings",
    session: Optional[AsyncSession],
) -> None:
    if session:
        attributes.flag_modified(user, "photos_json")
        await session.flush()
        return

    try:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(settings.db_url, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as temp_session:
            result = await temp_session.execute(select(User).where(User.id == user.id))
            db_user = result.scalar_one_or_none()
            if db_user:
                db_user.photos_json = user.photos_json
                attributes.flag_modified(db_user, "photos_json")
                await temp_session.commit()
        await engine.dispose()
    except Exception as exc:
        logger.error(
            "Failed to persist photo changes for user=%s: %s",
            user.telegram_id,
            exc,
        )


async def _notify_user_about_lost_photos(bot: "Bot", user: User) -> None:
    from app.services.core.rate_limiter import rate_limited_send

    try:
        await rate_limited_send(
            bot.send_message,
            chat_id=user.telegram_id,
            text=(
                "Похоже, ваши фото больше не доступны в нашем хранилище. "
                "Иногда такое бывает по техническим причинам.\n\n"
                "Пожалуйста, откройте /profile и загрузите фото заново."
            ),
        )
    except Exception as exc:
        logger.warning(
            "Failed to notify user=%s about lost photos: %s",
            user.telegram_id,
            exc,
        )


# ─────────────────── Чтение / запись данных фото ─────────────────── #


def get_photos_data(user: User) -> list[dict]:
    """
    Получает список фото-записей пользователя из БД.

    Поддерживает все форматы:
    - новый: [{"message_id": 123, "file_id": "AgAC..."}, ...]
    - устаревший (строки): ["1_ABC.jpg", ...] — пропускаются
    - устаревший (словари file_path): [{"file_path": "...", "ts": "..."}] — пропускаются

    Returns:
        list[dict]: список словарей {"message_id": int, "file_id": str}.
    """
    if not user.photos_json:
        return []

    photos = user.photos_json.get("photos", [])
    result = []
    for entry in photos:
        if isinstance(entry, dict) and "message_id" in entry:
            result.append(entry)
        # Устаревшие форматы — пропускаем
    return result


def set_photos_data(user: User, photos_data: list[dict]) -> None:
    """
    Сохраняет список фото-записей в photos_json пользователя.

    Args:
        user: объект пользователя.
        photos_data: список словарей {"message_id": int, "file_id": str}.
    """
    if photos_data:
        user.photos_json = {"photos": photos_data}
        attributes.flag_modified(user, "photos_json")
    else:
        user.photos_json = None


def get_photo_count(user: User) -> int:
    """Возвращает количество фото в профиле."""
    return len(get_photos_data(user))


def has_photos(user: User) -> bool:
    """Проверяет, есть ли у пользователя фото."""
    return get_photo_count(user) > 0


def can_add_photo(user: User) -> bool:
    """Проверяет, можно ли добавить ещё фото (лимит MAX_PHOTOS)."""
    return get_photo_count(user) < MAX_PHOTOS


# ─────────────────── Загрузка в чат-хранилище ─────────────────── #


async def upload_to_storage(
    bot: "Bot",
    photo: PhotoSize,
    storage_chat_id: int,
) -> Optional[dict]:
    """
    Загружает фото в чат-хранилище.

    Отправляет фото по file_id в чат-хранилище и возвращает
    словарь с message_id и file_id для сохранения в БД.

    Args:
        bot: объект бота aiogram.
        photo: объект PhotoSize (от пользователя).
        storage_chat_id: ID чата-хранилища.

    Returns:
        dict | None: {"message_id": int, "file_id": str} или None при ошибке.
    """
    try:
        message = await bot.send_photo(
            chat_id=storage_chat_id,
            photo=photo.file_id,
        )
        if message.photo:
            return {
                "message_id": message.message_id,
                "file_id": message.photo[-1].file_id,
            }
        return None
    except Exception as e:
        logger.error("Ошибка загрузки фото в хранилище: %s", e)
        return None


async def refresh_file_id(
    bot: "Bot",
    storage_chat_id: int,
    message_id: int,
    timeout: float = 5.0,
    max_retries: int = 3,
) -> tuple[str, Optional[str]]:
    """Refresh file_id from message_id without mutating user data."""
    from app.services.core.rate_limiter import rate_limited_send

    if not message_id:
        return PHOTO_CHECK_BROKEN_RECORD, None

    for attempt in range(1, max_retries + 1):
        try:
            forwarded = await asyncio.wait_for(
                rate_limited_send(
                    bot.forward_message,
                    chat_id=storage_chat_id,
                    from_chat_id=storage_chat_id,
                    message_id=message_id,
                ),
                timeout=timeout,
            )
            try:
                if not forwarded.photo:
                    logger.warning(
                        "message_id=%s forwarded without photo while refreshing file_id",
                        message_id,
                    )
                    return PHOTO_CHECK_BROKEN_RECORD, None
                return PHOTO_CHECK_OK, forwarded.photo[-1].file_id
            finally:
                try:
                    await rate_limited_send(
                        bot.delete_message,
                        chat_id=storage_chat_id,
                        message_id=forwarded.message_id,
                    )
                except Exception:
                    pass
        except asyncio.TimeoutError:
            if attempt < max_retries:
                logger.warning(
                    "Timeout refreshing file_id for message_id=%s, attempt %d/%d",
                    message_id,
                    attempt,
                    max_retries,
                )
                await asyncio.sleep(min(attempt, 3))
                continue
            return PHOTO_CHECK_TRANSIENT_ERROR, None
        except TelegramBadRequest as exc:
            if _is_message_missing_error(exc):
                logger.warning("message_id=%s is not available: %s", message_id, exc)
                return PHOTO_CHECK_MESSAGE_MISSING, None
            if attempt < max_retries:
                logger.warning(
                    "Telegram error while refreshing file_id for message_id=%s, attempt %d/%d: %s",
                    message_id,
                    attempt,
                    max_retries,
                    exc,
                )
                await asyncio.sleep(min(attempt, 3))
                continue
            return PHOTO_CHECK_TRANSIENT_ERROR, None
        except Exception as exc:
            if attempt < max_retries:
                logger.warning(
                    "Unexpected error while refreshing file_id for message_id=%s, attempt %d/%d: %s",
                    message_id,
                    attempt,
                    max_retries,
                    exc,
                )
                await asyncio.sleep(min(attempt, 3))
                continue
            return PHOTO_CHECK_TRANSIENT_ERROR, None

    return PHOTO_CHECK_TRANSIENT_ERROR, None


async def _check_file_id(
    bot: "Bot",
    storage_chat_id: int,
    file_id: Optional[str],
    timeout: float = 5.0,
    max_retries: int = 3,
) -> tuple[str, Optional[str]]:
    from app.services.core.rate_limiter import rate_limited_send

    if not file_id:
        return PHOTO_CHECK_BROKE_FILE_ID, None

    for attempt in range(1, max_retries + 1):
        try:
            probe = await asyncio.wait_for(
                rate_limited_send(
                    bot.send_photo,
                    chat_id=storage_chat_id,
                    photo=file_id,
                ),
                timeout=timeout,
            )
            try:
                if probe.photo:
                    return PHOTO_CHECK_OK, probe.photo[-1].file_id
                return PHOTO_CHECK_OK, file_id
            finally:
                try:
                    await rate_limited_send(
                        bot.delete_message,
                        chat_id=storage_chat_id,
                        message_id=probe.message_id,
                    )
                except Exception:
                    pass
        except asyncio.TimeoutError:
            if attempt < max_retries:
                logger.warning(
                    "Timeout checking file_id, attempt %d/%d",
                    attempt,
                    max_retries,
                )
                await asyncio.sleep(min(attempt, 3))
                continue
            return PHOTO_CHECK_TRANSIENT_ERROR, None
        except TelegramBadRequest as exc:
            if _is_bad_file_error(exc):
                return PHOTO_CHECK_BROKE_FILE_ID, None
            if attempt < max_retries:
                logger.warning(
                    "Telegram error while checking file_id, attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    exc,
                )
                await asyncio.sleep(min(attempt, 3))
                continue
            return PHOTO_CHECK_TRANSIENT_ERROR, None
        except Exception as exc:
            if attempt < max_retries:
                logger.warning(
                    "Unexpected error while checking file_id, attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    exc,
                )
                await asyncio.sleep(min(attempt, 3))
                continue
            return PHOTO_CHECK_TRANSIENT_ERROR, None

    return PHOTO_CHECK_TRANSIENT_ERROR, None


async def _refresh_photo_entry_from_message_id(
    bot: "Bot",
    user: User,
    photo_entry: dict,
    settings: "Settings",
) -> tuple[str, bool]:
    if not settings.photos_storage_chat_id:
        logger.warning(
            "photos_storage_chat_id is not configured, cannot refresh file_id for user=%s",
            user.telegram_id,
        )
        return PHOTO_CHECK_TRANSIENT_ERROR, False

    status, new_file_id = await refresh_file_id(
        bot,
        settings.photos_storage_chat_id,
        photo_entry.get("message_id"),
    )
    if status == PHOTO_CHECK_OK and new_file_id:
        changed = photo_entry.get("file_id") != new_file_id
        photo_entry["file_id"] = new_file_id
        return (
            PHOTO_CHECK_REFRESHED if changed else PHOTO_CHECK_OK,
            changed,
        )

    if status in (PHOTO_CHECK_MESSAGE_MISSING, PHOTO_CHECK_BROKEN_RECORD):
        _append_photo_issue_log(user, photo_entry, status)

    return status, False


async def _copy_photo_by_message_id(
    bot: "Bot",
    chat_id: int,
    storage_chat_id: int,
    photo_entry: dict,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = None,
    timeout: float = 8.0,
    max_retries: int = 3,
) -> str:
    from app.services.core.rate_limiter import rate_limited_send

    message_id = photo_entry.get("message_id")
    if not message_id:
        return PHOTO_CHECK_BROKEN_RECORD

    kwargs = {
        "chat_id": chat_id,
        "from_chat_id": storage_chat_id,
        "message_id": message_id,
    }
    if caption is not None:
        kwargs["caption"] = caption
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode

    for attempt in range(1, max_retries + 1):
        try:
            await asyncio.wait_for(
                rate_limited_send(bot.copy_message, **kwargs),
                timeout=timeout,
            )
            return PHOTO_CHECK_OK
        except asyncio.TimeoutError:
            if attempt < max_retries:
                logger.warning(
                    "Timeout copying message_id=%s, attempt %d/%d",
                    message_id,
                    attempt,
                    max_retries,
                )
                await asyncio.sleep(min(attempt, 3))
                continue
            return PHOTO_CHECK_TRANSIENT_ERROR
        except TelegramBadRequest as exc:
            if _is_message_missing_error(exc):
                return PHOTO_CHECK_MESSAGE_MISSING
            if attempt < max_retries:
                logger.warning(
                    "Telegram error while copying message_id=%s, attempt %d/%d: %s",
                    message_id,
                    attempt,
                    max_retries,
                    exc,
                )
                await asyncio.sleep(min(attempt, 3))
                continue
            return PHOTO_CHECK_TRANSIENT_ERROR
        except Exception as exc:
            if attempt < max_retries:
                logger.warning(
                    "Unexpected error while copying message_id=%s, attempt %d/%d: %s",
                    message_id,
                    attempt,
                    max_retries,
                    exc,
                )
                await asyncio.sleep(min(attempt, 3))
                continue
            return PHOTO_CHECK_TRANSIENT_ERROR

    return PHOTO_CHECK_TRANSIENT_ERROR


# ─────────────────── Валидация и обновление фото ─────────────────── #


async def validate_and_refresh_photos(
    bot: "Bot",
    user: User,
    settings: "Settings",
    session: Optional[AsyncSession] = None,
) -> bool:
    """Soft validation: refresh file_id when possible and never clear user data."""
    photos_data = get_photos_data(user)
    if not photos_data:
        return False

    if not settings.photos_storage_chat_id:
        logger.warning(
            "photos_storage_chat_id is not configured, skip photo validation for user=%s",
            user.telegram_id,
        )
        return any(entry.get("file_id") for entry in photos_data)

    active_entries: list[dict] = []
    changed = False
    removed_count = 0
    usable_photos = 0

    for idx, photo_entry in enumerate(photos_data, start=1):
        file_id = photo_entry.get("file_id")
        entry_had_file_id = bool(file_id)

        if file_id:
            status, checked_file_id = await _check_file_id(
                bot,
                settings.photos_storage_chat_id,
                file_id,
            )
            if status == PHOTO_CHECK_OK:
                usable_photos += 1
                if checked_file_id and checked_file_id != file_id:
                    photo_entry["file_id"] = checked_file_id
                    changed = True
                active_entries.append(photo_entry)
                continue
            if status == PHOTO_CHECK_TRANSIENT_ERROR:
                logger.warning(
                    "[%d/%d] transient error while checking file_id for user=%s, keep current data",
                    idx,
                    len(photos_data),
                    user.telegram_id,
                )
                usable_photos += 1
                active_entries.append(photo_entry)
                continue

        status, entry_changed = await _refresh_photo_entry_from_message_id(
            bot,
            user,
            photo_entry,
            settings,
        )
        if status in (PHOTO_CHECK_OK, PHOTO_CHECK_REFRESHED):
            usable_photos += 1
            changed = changed or entry_changed
            active_entries.append(photo_entry)
            continue

        if status == PHOTO_CHECK_TRANSIENT_ERROR:
            logger.warning(
                "[%d/%d] transient error while refreshing file_id for user=%s, keep current data",
                idx,
                len(photos_data),
                user.telegram_id,
            )
            if entry_had_file_id:
                usable_photos += 1
            active_entries.append(photo_entry)
            continue

        logger.warning(
            "[%d/%d] photo source is unavailable for user=%s, record removed",
            idx,
            len(photos_data),
            user.telegram_id,
        )
        removed_count += 1
        changed = True

    if changed or removed_count:
        set_photos_data(user, active_entries)
        await _persist_photo_changes(user, settings, session)
        if removed_count and not active_entries:
            await _notify_user_about_lost_photos(bot, user)

    return usable_photos > 0


# ─────────────────── Построение медиа-группы ─────────────────── #


async def build_user_media_group(
    bot: "Bot",
    user: User,
    settings: "Settings",
    caption: Optional[str] = None,
    parse_mode: Optional[str] = None,
    session: Optional[AsyncSession] = None,
    skip_validation: bool = False,
) -> list[InputMediaPhoto]:
    """Build media group from currently known file_id values."""
    if not skip_validation:
        try:
            await validate_and_refresh_photos(bot, user, settings, session)
        except Exception as exc:
            logger.warning(
                "Soft validation failed before building media group for user=%s: %s",
                user.telegram_id,
                exc,
            )

    photos_data = get_photos_data(user)
    if not photos_data:
        return []

    media_group: list[InputMediaPhoto] = []
    first_caption_used = False

    for photo_entry in photos_data:
        file_id = photo_entry.get("file_id")
        if not file_id:
            continue

        media_group.append(
            InputMediaPhoto(
                media=file_id,
                caption=caption if not first_caption_used else None,
                parse_mode=parse_mode if caption and not first_caption_used else None,
            )
        )
        first_caption_used = True

    return media_group


# ─────────────────── Отправка фото пользователю ─────────────────── #


async def _send_photo_entry_with_recovery(
    bot: "Bot",
    chat_id: int,
    user: User,
    photo_entry: dict,
    settings: "Settings",
    caption: Optional[str] = None,
    parse_mode: Optional[str] = None,
) -> tuple[str, bool]:
    from app.services.core.rate_limiter import rate_limited_send

    file_id = photo_entry.get("file_id")
    if file_id:
        try:
            await asyncio.wait_for(
                rate_limited_send(
                    bot.send_photo,
                    chat_id=chat_id,
                    photo=file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                ),
                timeout=8.0,
            )
            return PHOTO_CHECK_OK, False
        except TelegramBadRequest as exc:
            if not _is_bad_file_error(exc):
                logger.warning(
                    "Telegram error while sending photo by file_id for user=%s: %s",
                    user.telegram_id,
                    exc,
                )
                return PHOTO_CHECK_TRANSIENT_ERROR, False
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout while sending photo by file_id for user=%s",
                user.telegram_id,
            )
            return PHOTO_CHECK_TRANSIENT_ERROR, False
        except Exception as exc:
            logger.warning(
                "Unexpected error while sending photo by file_id for user=%s: %s",
                user.telegram_id,
                exc,
            )
            return PHOTO_CHECK_TRANSIENT_ERROR, False

    if not settings.photos_storage_chat_id:
        logger.warning(
            "photos_storage_chat_id is not configured, cannot recover photo for user=%s",
            user.telegram_id,
        )
        return PHOTO_CHECK_TRANSIENT_ERROR, False

    copy_status = await _copy_photo_by_message_id(
        bot,
        chat_id,
        settings.photos_storage_chat_id,
        photo_entry,
        caption=caption,
        parse_mode=parse_mode,
    )
    if copy_status != PHOTO_CHECK_OK:
        if copy_status in (PHOTO_CHECK_MESSAGE_MISSING, PHOTO_CHECK_BROKEN_RECORD):
            _append_photo_issue_log(user, photo_entry, copy_status)
        return copy_status, False

    refresh_status, entry_changed = await _refresh_photo_entry_from_message_id(
        bot,
        user,
        photo_entry,
        settings,
    )
    if refresh_status == PHOTO_CHECK_TRANSIENT_ERROR:
        logger.warning(
            "Photo for user=%s was sent by message_id, but file_id refresh failed temporarily",
            user.telegram_id,
        )

    return (
        PHOTO_CHECK_REFRESHED if entry_changed else PHOTO_CHECK_OK,
        entry_changed,
    )


async def send_user_photos(
    bot: "Bot",
    chat_id: int,
    user: User,
    settings: "Settings",
    caption: Optional[str] = None,
    parse_mode: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> bool:
    from app.services.core.rate_limiter import rate_limited_send

    photos_data = get_photos_data(user)
    if not photos_data:
        return False

    original_count = len(photos_data)
    if len(photos_data) > 1 and all(entry.get("file_id") for entry in photos_data):
        media_group = [
            InputMediaPhoto(
                media=entry["file_id"],
                caption=caption if idx == 0 else None,
                parse_mode=parse_mode if idx == 0 and caption else None,
            )
            for idx, entry in enumerate(photos_data)
        ]
        try:
            await asyncio.wait_for(
                rate_limited_send(
                    bot.send_media_group,
                    chat_id=chat_id,
                    media=media_group,
                ),
                timeout=10.0,
            )
            return True
        except TelegramBadRequest as exc:
            if not _is_bad_file_error(exc):
                logger.warning(
                    "Cannot send media group for user=%s: %s",
                    user.telegram_id,
                    exc,
                )
                return False
            logger.info(
                "Media group for user=%s contains stale file_id, fallback to per-photo recovery",
                user.telegram_id,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout while sending media group for user=%s",
                user.telegram_id,
            )
            return False
        except Exception as exc:
            logger.warning(
                "Unexpected error while sending media group for user=%s: %s",
                user.telegram_id,
                exc,
            )
            return False

    active_entries: list[dict] = []
    changed = False
    sent_any = False

    for photo_entry in photos_data:
        current_caption = caption if not sent_any else None
        current_parse_mode = parse_mode if current_caption else None
        status, entry_changed = await _send_photo_entry_with_recovery(
            bot,
            chat_id,
            user,
            photo_entry,
            settings,
            caption=current_caption,
            parse_mode=current_parse_mode,
        )
        changed = changed or entry_changed

        if status in (PHOTO_CHECK_OK, PHOTO_CHECK_REFRESHED):
            sent_any = True
            active_entries.append(photo_entry)
            continue

        if status in (PHOTO_CHECK_MESSAGE_MISSING, PHOTO_CHECK_BROKEN_RECORD):
            logger.warning(
                "Photo source is unavailable for user=%s, remove current photo from profile",
                user.telegram_id,
            )
            changed = True
            continue

        logger.warning(
            "Temporary error while sending photo for user=%s, skip current photo",
            user.telegram_id,
        )
        active_entries.append(photo_entry)

    removed_count = original_count - len(active_entries)
    if changed or removed_count:
        set_photos_data(user, active_entries)
        await _persist_photo_changes(user, settings, session)
        if removed_count and not active_entries:
            await _notify_user_about_lost_photos(bot, user)

    if not sent_any:
        logger.warning("No photos were sent for user=%s", user.telegram_id)

    return sent_any


# ─────────────────── Управление фото в профиле ─────────────────── #


async def add_single_photo(
    session: AsyncSession,
    user: User,
    photo: PhotoSize,
    bot: "Bot",
    settings: "Settings",
) -> bool:
    """
    Добавляет одно фото в профиль пользователя.

    Загружает фото в чат-хранилище и сохраняет message_id + file_id в БД.

    Args:
        session: сессия БД.
        user: объект пользователя.
        photo: PhotoSize от пользователя.
        bot: объект бота.
        settings: настройки.

    Returns:
        bool: True если фото добавлено, False если лимит или ошибка.
    """
    if not user.telegram_id:
        return False

    photos_data = get_photos_data(user)
    if len(photos_data) >= MAX_PHOTOS:
        return False

    if not settings.photos_storage_chat_id:
        logger.error("PHOTOS_STORAGE_CHAT_ID не задан")
        return False

    entry = await upload_to_storage(bot, photo, settings.photos_storage_chat_id)
    if not entry:
        return False

    photos_data.append(entry)
    set_photos_data(user, photos_data)
    await session.commit()
    return True


async def add_photos(
    session: AsyncSession,
    user: User,
    photos: list[PhotoSize],
    bot: "Bot",
    settings: "Settings",
) -> tuple[bool, int]:
    """
    Добавляет несколько фото в профиль (альбом).

    Args:
        session: сессия БД.
        user: объект пользователя.
        photos: список PhotoSize.
        bot: объект бота.
        settings: настройки.

    Returns:
        tuple[bool, int]: (уложились_в_лимит, кол-во_добавленных).
            False означает, что свободных слотов нет.
    """
    if not user.telegram_id:
        return False, 0

    photos_data = get_photos_data(user)
    free_slots = MAX_PHOTOS - len(photos_data)

    if free_slots <= 0:
        return False, 0

    if not settings.photos_storage_chat_id:
        logger.error("PHOTOS_STORAGE_CHAT_ID не задан")
        return False, 0

    added = 0
    for photo in photos[:free_slots]:
        entry = await upload_to_storage(
            bot, photo, settings.photos_storage_chat_id
        )
        if entry:
            photos_data.append(entry)
            added += 1

    if added:
        set_photos_data(user, photos_data)
        await session.commit()

    return True, added


async def get_telegram_profile_photo(
    bot: "Bot",
    user_id: int,
) -> Optional[PhotoSize]:
    """
    Получает фото профиля пользователя из Telegram.

    Args:
        bot: объект бота.
        user_id: Telegram ID пользователя.

    Returns:
        PhotoSize | None: объект фото или None.
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
    bot: "Bot",
    settings: "Settings",
) -> bool:
    """
    Добавляет фото из Telegram-профиля в профиль пользователя.

    Args:
        session: сессия БД.
        user: объект пользователя.
        photo: PhotoSize из профиля Telegram.
        bot: объект бота.
        settings: настройки.

    Returns:
        bool: True если добавлено.
    """
    return await add_single_photo(session, user, photo, bot, settings)


async def clear_user_photos(
    session: AsyncSession,
    user: User,
) -> None:
    """
    Очищает все фото пользователя из БД.

    Фото в чате-хранилище остаются (служат бэкапом).

    Args:
        session: сессия БД.
        user: объект пользователя.
    """
    user.photos_json = None
    user.stage = "profile_photo"
    await session.commit()


# ─────────────────── UI-хелпер ─────────────────── #


async def send_photo_request(
    message_or_cq,
    state,
    kb=None,
) -> None:
    """
    Отправляет стандартный запрос на загрузку фото с клавиатурой.

    Args:
        message_or_cq: Message или CallbackQuery.
        state: FSMContext.
        kb: клавиатура (по умолчанию kb_profile_photo()).
    """
    from app.keyboards.kb_profile import kb_profile_photo as default_kb
    from app.handlers.fsm import FSMDataKeys

    if kb is None:
        kb = default_kb()

    if hasattr(message_or_cq, "message"):
        sent = await message_or_cq.message.answer(
            "Выбери фото и пришли их ниже 👇",
            reply_markup=kb,
        )
    else:
        sent = await message_or_cq.answer(
            "Выбери фото и пришли их ниже 👇",
            reply_markup=kb,
        )

    if sent and hasattr(sent, "message_id"):
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
