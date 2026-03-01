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

import logging
from typing import Optional, TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaPhoto, PhotoSize
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

if TYPE_CHECKING:
    from aiogram import Bot
    from app.services.core.config import Settings

from app.database import User
from app.services.const import USER_STATUS_NOT_ACTIVE

logger = logging.getLogger(__name__)

# Максимальное количество фото в профиле
MAX_PHOTOS = 5


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
) -> Optional[str]:
    """
    Обновляет file_id через message_id из чата-хранилища.

    Пересылает сообщение в тот же чат, извлекает file_id из пересланного
    сообщения, затем удаляет пересланное сообщение.

    Args:
        bot: объект бота.
        storage_chat_id: ID чата-хранилища.
        message_id: ID сообщения с фото.

    Returns:
        str | None: обновлённый file_id или None при ошибке (включая невалидный message_id).
    """
    from aiogram.exceptions import TelegramBadRequest
    
    try:
        forwarded = await bot.forward_message(
            chat_id=storage_chat_id,
            from_chat_id=storage_chat_id,
            message_id=message_id,
        )
        file_id = (
            forwarded.photo[-1].file_id if forwarded.photo else None
        )
        # Удаляем пересланное сообщение (уборка)
        try:
            await bot.delete_message(storage_chat_id, forwarded.message_id)
        except Exception:
            pass
        return file_id
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        # Проверяем, является ли ошибка "message not found"
        if "message" in error_msg and ("not found" in error_msg or "to forward not found" in error_msg):
            logger.warning(
                "message_id=%s невалидный (сообщение не найдено): %s", message_id, e
            )
        else:
            logger.error(
                "Ошибка Telegram API при обновлении file_id (message_id=%s): %s", message_id, e
            )
        return None
    except Exception as e:
        logger.error(
            "Неожиданная ошибка при обновлении file_id (message_id=%s): %s", message_id, e
        )
        return None


# ─────────────────── Валидация и обновление фото ─────────────────── #


async def validate_and_refresh_photos(
    bot: "Bot",
    user: User,
    settings: "Settings",
    session: Optional[AsyncSession] = None,
) -> bool:
    """
    Валидирует и обновляет все file_id через message_id.
    
    Правила:
    1. Если file_id отсутствует или невалидный → обновляется через message_id
    2. Если message_id невалидный (сообщение не найдено) → запись удаляется
    3. Если все записи невалидны → photos_json очищается (становится None)
    4. Изменения сохраняются в БД, если передан session
    
    Args:
        bot: объект бота.
        user: объект пользователя.
        settings: настройки (содержат photos_storage_chat_id).
        session: сессия БД — если передана, изменения будут сохранены.
    
    Returns:
        bool: True если есть валидные фото, False если photos_json очищен.
    """
    photos_data = get_photos_data(user)
    if not photos_data:
        return False
    
    if not settings.photos_storage_chat_id:
        logger.warning(
            "photos_storage_chat_id не задан, невозможно обновить file_id для user=%s",
            user.telegram_id
        )
        return False
    
    logger.debug(
        "Начинаю валидацию %d записей фото для user=%s",
        len(photos_data), user.telegram_id
    )
    
    valid_entries = []
    has_missing_message_id = False  # Флаг: есть ли записи без message_id
    
    for idx, photo_entry in enumerate(photos_data, start=1):
        message_id = photo_entry.get("message_id")
        
        if not message_id:
            # Нет message_id - запись невалидна, пропускаем
            has_missing_message_id = True
            logger.warning(
                "[%d/%d] Запись фото без message_id для user=%s - запись будет удалена",
                idx, len(photos_data), user.telegram_id
            )
            continue
        
        # Пытаемся обновить file_id через message_id
        new_file_id = await refresh_file_id(
            bot, settings.photos_storage_chat_id, message_id
        )
        
        if new_file_id:
            # message_id валидный, file_id обновлён
            photo_entry["file_id"] = new_file_id
            valid_entries.append(photo_entry)
            logger.debug(
                "[%d/%d] Обновлён file_id для user=%s через message_id=%s",
                idx, len(photos_data), user.telegram_id, message_id
            )
        else:
            # message_id невалидный (сообщение не найдено) - запись удаляется
            logger.warning(
                "[%d/%d] message_id=%s невалидный для user=%s (сообщение не найдено) - запись будет удалена",
                idx, len(photos_data), message_id, user.telegram_id
            )
    
    # Если есть записи без message_id или не осталось валидных записей - очищаем photos_json
    if has_missing_message_id or not valid_entries:
        if has_missing_message_id:
            logger.error(
                "Обнаружены записи без message_id для user=%s. Очищаю photos_json, устанавливаю stage=profile_photo, status=not_active.",
                user.telegram_id
            )
        else:
            logger.error(
                "Все %d фото невалидны для user=%s (нет валидных message_id). Очищаю photos_json, устанавливаю stage=profile_photo, status=not_active.",
                len(photos_data), user.telegram_id
            )
        
        user.photos_json = None
        user.stage = "profile_photo"
        user.status = USER_STATUS_NOT_ACTIVE
        
        # Сохраняем изменения в БД
        if session:
            try:
                await session.commit()
                logger.info(
                    "photos_json очищен, stage=profile_photo, status=not_active для user=%s и сохранены в БД",
                    user.telegram_id
                )
            except Exception as e:
                logger.error(
                    "Ошибка очистки photos_json в БД (user=%s): %s",
                    user.telegram_id, e
                )
                raise
        else:
            # Если session не передан, создаём новую сессию для сохранения
            try:
                from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
                from sqlalchemy import select
                
                # Создаём временную сессию для сохранения
                engine = create_async_engine(settings.db_url, echo=False)
                session_factory = async_sessionmaker(engine, expire_on_commit=False)
                
                async with session_factory() as temp_session:
                    # Загружаем пользователя заново в новой сессии
                    result = await temp_session.execute(
                        select(User).where(User.id == user.id)
                    )
                    db_user = result.scalar_one_or_none()
                    if db_user:
                        db_user.photos_json = None
                        db_user.stage = "profile_photo"
                        db_user.status = USER_STATUS_NOT_ACTIVE
                        await temp_session.commit()
                        logger.info(
                            "photos_json очищен, stage=profile_photo, status=not_active для user=%s и сохранены в БД (создана новая сессия)",
                            user.telegram_id
                        )
                    else:
                        logger.warning(
                            "Пользователь user=%s не найден в БД для очистки photos_json",
                            user.telegram_id
                        )
                await engine.dispose()
            except Exception as e:
                logger.error(
                    "Ошибка создания сессии и очистки photos_json в БД (user=%s): %s",
                    user.telegram_id, e
                )
                # Не пробрасываем ошибку, так как это не критично для работы бота
        
        return False
    
    # Обновляем photos_json только валидными записями
    if len(valid_entries) < len(photos_data):
        # Были удалены невалидные записи
        logger.info(
            "Удалено %d невалидных записей для user=%s, осталось %d валидных",
            len(photos_data) - len(valid_entries),
            user.telegram_id,
            len(valid_entries)
        )
    
    set_photos_data(user, valid_entries)
    
    # Сохраняем изменения в БД
    if session:
        try:
            await session.commit()
            logger.debug(
                "Обновлены file_id для user=%s (%d записей)",
                user.telegram_id, len(valid_entries)
            )
        except Exception as e:
            logger.error(
                "Ошибка сохранения обновлённых file_id в БД (user=%s): %s",
                user.telegram_id, e
            )
    else:
        # Если session не передан, но были изменения - создаём новую сессию для сохранения
        try:
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
            from sqlalchemy import select
            
            engine = create_async_engine(settings.db_url, echo=False)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            
            async with session_factory() as temp_session:
                result = await temp_session.execute(
                    select(User).where(User.id == user.id)
                )
                db_user = result.scalar_one_or_none()
                if db_user:
                    # Обновляем photos_json в БД
                    db_user.photos_json = user.photos_json
                    attributes.flag_modified(db_user, "photos_json")
                    await temp_session.commit()
                    logger.debug(
                        "Обновлены file_id для user=%s (%d записей) - создана новая сессия",
                        user.telegram_id, len(valid_entries)
                    )
            await engine.dispose()
        except Exception as e:
            logger.error(
                "Ошибка создания сессии и сохранения обновлённых file_id в БД (user=%s): %s",
                user.telegram_id, e
            )
    
    return True


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
    """
    Строит медиа-группу из фото пользователя.

    Использует только валидные file_id для построения медиа-группы.
    Если skip_validation=False, валидирует и обновляет все file_id перед построением.

    Args:
        bot: объект бота.
        user: объект пользователя.
        settings: настройки приложения (содержат photos_storage_chat_id).
        caption: подпись к первому фото.
        parse_mode: режим парсинга подписи (HTML, Markdown, ...).
        session: сессия БД — если передана, изменения будут сохранены.
        skip_validation: пропустить валидацию (если уже выполнена ранее).

    Returns:
        list[InputMediaPhoto]: список элементов для send_media_group/send_photo.
    """
    # Валидируем и обновляем все фото перед построением медиа-группы (если не пропущено)
    if not skip_validation:
        has_valid_photos = await validate_and_refresh_photos(bot, user, settings, session)
        if not has_valid_photos:
            return []
    
    photos_data = get_photos_data(user)
    if not photos_data:
        return []

    media_group: list[InputMediaPhoto] = []

    for idx, photo_entry in enumerate(photos_data):
        file_id = photo_entry.get("file_id")
        if not file_id:
            # Это не должно происходить после validate_and_refresh_photos,
            # но на всякий случай проверяем
            continue
        
        photo_caption = caption if idx == 0 else None
        photo_parse_mode = parse_mode if idx == 0 and caption else None

        media_group.append(
            InputMediaPhoto(
                media=file_id,
                caption=photo_caption,
                parse_mode=photo_parse_mode,
            )
        )

    return media_group


# ─────────────────── Отправка фото пользователю ─────────────────── #


async def send_user_photos(
    bot: "Bot",
    chat_id: int,
    user: User,
    settings: "Settings",
    caption: Optional[str] = None,
    parse_mode: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> None:
    """
    Отправляет фото пользователя в чат.

    Перед отправкой валидирует и обновляет все file_id через validate_and_refresh_photos.
    Одиночное фото — send_photo, несколько — send_media_group.
    При ошибке отправки (невалидный file_id) повторно валидирует и обновляет.

    Args:
        bot: объект бота.
        chat_id: ID чата для отправки.
        user: объект пользователя (читает photos_json).
        settings: настройки (содержат photos_storage_chat_id).
        caption: подпись к первому фото.
        parse_mode: режим парсинга подписи.
        session: сессия БД — если передана, обновлённые file_id будут сохранены.
    """
    # Валидируем и обновляем все фото перед отправкой
    has_valid_photos = await validate_and_refresh_photos(bot, user, settings, session)
    if not has_valid_photos:
        return
    
    # Строим медиа-группу (пропускаем валидацию, так как уже выполнили)
    media_group = await build_user_media_group(
        bot, user, settings, caption, parse_mode, session, skip_validation=True
    )
    if not media_group:
        return

    try:
        # Используем rate limiting для вызовов Telegram API
        from app.services.core.rate_limiter import rate_limited_send
        
        if len(media_group) == 1:
            item = media_group[0]
            await rate_limited_send(
                bot.send_photo,
                chat_id,
                photo=item.media,
                caption=item.caption,
                parse_mode=item.parse_mode,
            )
        else:
            try:
                await rate_limited_send(
                    bot.send_media_group,
                    chat_id,
                    media=media_group
                )
            except TelegramBadRequest:
                # Fallback: отправляем по одному
                for item in media_group:
                    await rate_limited_send(
                        bot.send_photo,
                        chat_id,
                        photo=item.media,
                        caption=item.caption,
                        parse_mode=item.parse_mode,
                    )
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        if (
            "wrong file" in error_msg
            or "file_reference" in error_msg
            or "can't unserialize" in error_msg
            or "wrong remote file identifier" in error_msg
        ):
            # file_id устарел — повторно валидируем и обновляем все фото
            logger.info(
                "Обнаружен устаревший file_id для user=%s, повторно валидирую и обновляю...",
                user.telegram_id
            )
            try:
                # Повторно валидируем и обновляем все фото
                has_valid_photos = await validate_and_refresh_photos(
                    bot, user, settings, session
                )
                if not has_valid_photos:
                    logger.warning(
                        "После валидации не осталось валидных фото для user=%s",
                        user.telegram_id
                    )
                    return
                
                # Строим медиа-группу заново с обновлёнными file_id (пропускаем валидацию)
                media_group = await build_user_media_group(
                    bot, user, settings, caption, parse_mode, session, skip_validation=True
                )
                if not media_group:
                    return
                
                # Повторная отправка
                if len(media_group) == 1:
                    item = media_group[0]
                    await rate_limited_send(
                        bot.send_photo,
                        chat_id,
                        photo=item.media,
                        caption=item.caption,
                        parse_mode=item.parse_mode,
                    )
                else:
                    await rate_limited_send(
                        bot.send_media_group,
                        chat_id,
                        media=media_group
                    )
                
                logger.info(
                    "Успешно обновлён и отправлен file_id для user=%s", user.telegram_id
                )
            except Exception as refresh_error:
                logger.error(
                    "Не удалось обновить и отправить фото для user=%s: %s",
                    user.telegram_id, refresh_error
                )
                raise
        else:
            logger.error("Ошибка отправки фото user=%s: %s", user.telegram_id, e)

    # Сохраняем обновлённые file_id в БД
    if session:
        try:
            await session.commit()
        except Exception:
            pass


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
