"""
Обработчики для работы с фотографиями в профиле пользователя.

Реализует handlers для загрузки и управления фотографиями профиля:
обработка медиа-групп, одиночных фото и callback-запросов.
"""

from __future__ import annotations

import asyncio

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.core import Settings
from app.services.core.users import get_or_create_user
from app.services.core.text import send_photo_request
from app.database.utils import now_utc
from app.keyboards.kb_profile import (
    kb_profile_photo,
    kb_profile_photo_with_photos,
    kb_profile_photo_clear_save,
    kb_profile_review,
)
from app.services.profile.utils import get_photos_list, send_photos
from app.services.profile.utils import is_profile_complete
from app.services.profile.preview import _send_profile_preview
from app.services.profile.photo import (
    MAX_PHOTOS,
    add_to_media_group_buffer,
    get_and_clear_media_group_buffer,
    is_media_group_processing,
    set_media_group_task,
    remove_media_group_task,
    add_photos_to_profile,
    add_single_photo_to_profile,
    get_telegram_profile_photo,
    add_telegram_profile_photo,
    clear_user_photos,
    can_add_photo,
)

router = Router()


# ----------------------- Media Group Handler ---------------------- #


@router.message(F.media_group_id)
async def on_media_group(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает несколько фото, присланных одним альбомом.

    Телеграм шлёт альбом как несколько сообщений с одинаковым media_group_id.
    Здесь мы складываем все кадры в буфер, а отдельная задача через небольшую
    паузу собирает альбом целиком и сохраняет фото в профиль.
    """
    media_group_id = str(message.media_group_id)

    # Сохраняем текущий кадр в буфере
    if message.photo:
        add_to_media_group_buffer(media_group_id, message.photo[-1])

    # Если обработка этого media_group уже запущена — просто выходим
    if is_media_group_processing(media_group_id):
        return

    # Стартуем отдельную задачу, которая "дождётся" остальные кадры
    task = asyncio.create_task(
        _finalize_media_group_album(
            bot=message.bot,
            media_group_id=media_group_id,
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            state=state,
            session_factory=session_factory,
        )
    )
    set_media_group_task(media_group_id, task)


async def _finalize_media_group_album(
    bot,
    media_group_id: str,
    user_id: int,
    chat_id: int,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Дождаться прихода всех сообщений альбома и обработать их одним разом.
    """
    try:
        # Даём Телеграму время прислать все сообщения альбома
        await asyncio.sleep(0.7)

        photos = get_and_clear_media_group_buffer(media_group_id)
        if not photos:
            return

        async with session_factory() as session:
            user = await get_or_create_user(session, user_id)
            user.last_activity = now_utc()

            # Добавляем фото через бизнес-логику
            success, photos_list = await add_photos_to_profile(session, user, photos)

            if not success:
                # Удаляем старую клавиатуру
                data = await state.get_data()
                last_kb_mid = data.get("last_kb_mid")
                if last_kb_mid:
                    try:
                        await bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=last_kb_mid,
                            reply_markup=None,
                        )
                    except:
                        pass
                # Отправляем текущие фото
                await send_photos(bot, chat_id, photos_list)
                # Отправляем ошибку с кнопками
                await bot.send_message(
                    chat_id,
                    f"⚠️ Максимум {MAX_PHOTOS} фото. Очистите фото, чтобы добавить новые.",
                    reply_markup=kb_profile_photo_clear_save(),
                )
                return

        # Отправляем пользователю альбом (все актуальные фото профиля)
        await _send_photos_with_actions(bot, chat_id, user, state, photos_list)

    finally:
        # Освобождаем слот таска для этой media_group
        remove_media_group_task(media_group_id)


# ----------------------- Single Photo Handler --------------------- #


@router.message(F.photo & ~F.media_group_id)
async def on_single_photo(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает одиночное фото при загрузке.
    """
    async with session_factory() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )

        # Обрабатываем только при стадии profile_photo
        if user.stage != "profile_photo":
            await session.commit()
            raise SkipHandler()

        user.last_activity = now_utc()

        # Добавляем фото через бизнес-логику
        success, photos_list = await add_single_photo_to_profile(
            session, user, message.photo[-1]
        )

        if not success:
            # Удаляем старую клавиатуру
            data = await state.get_data()
            last_kb_mid = data.get("last_kb_mid")
            if last_kb_mid:
                try:
                    await message.bot.edit_message_reply_markup(
                        chat_id=message.chat.id,
                        message_id=last_kb_mid,
                        reply_markup=None,
                    )
                except:
                    pass
            # Отправляем текущие фото
            await send_photos(message.bot, message.chat.id, photos_list)
            # Отправляем ошибку с кнопками
            sent = await message.answer(
                f"⚠️ Максимум {MAX_PHOTOS} фото. Очистите фото, чтобы добавить новые.",
                reply_markup=kb_profile_photo_clear_save(),
            )
            await state.update_data(last_kb_mid=sent.message_id)
            return

        # Отправляем текущее количество фото и кнопки действий
        await _send_photos_with_actions(
            message.bot, message.chat.id, user, state, photos_list
        )


# ----------------------- Helper Functions ------------------------- #


async def _send_photos_with_actions(
    bot,
    chat_id: int,
    user,
    state: FSMContext,
    photos_list: list,
) -> None:
    """
    Отправляет сохранённые фото и отдельное сообщение
    с текстом о количестве и кнопками действий.
    """
    if not photos_list:
        await bot.send_message(
            chat_id,
            "Не удалось загрузить фото. Попробуйте ещё раз.",
        )
        return

    # Отправляем фото через универсальную функцию (с caption "Добавлено N фото")
    await _send_profile_preview(
        bot, chat_id, user, state, None, send_photos=True, send_preview_text=False
    )

    # Сообщение с кнопками (без текста "Добавлено N фото")
    keyboard = (
        kb_profile_photo_with_photos() if photos_list else kb_profile_photo()
    )
    sent = await bot.send_message(
        chat_id,
        "Выберите действие",
        reply_markup=keyboard,
    )

    await state.update_data(last_kb_mid=sent.message_id)


# ----------------------- Callback Handlers ----------------------- #


@router.callback_query(F.data == "prof:photo:from_tg")
async def cb_photo_from_tg(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Взять фото из профиля 👤».

    Пытается извлечь профильное фото пользователя из его Telegram профиля
    и добавить его в список фото анкеты. Если фото нет, уведомляет об этом.
    """
    async with session_factory() as session:
        user = await get_or_create_user(
            session, cq.from_user.id, cq.from_user.username
        )
        user.last_activity = now_utc()

        # Проверяем лимит
        if not can_add_photo(user):
            await session.commit()
            await cq.message.edit_text(
                f"⚠️ Достигнут максимум из {MAX_PHOTOS} фото.",
                reply_markup=kb_profile_photo_clear_save(),
            )
            await cq.answer()
            return

        # Получаем фото из профиля через бизнес-логику
        photo = await get_telegram_profile_photo(cq.bot, cq.from_user.id)

        if not photo:
            await session.commit()
            await cq.message.delete()
            await send_photo_request(cq, state, kb_profile_photo())
            await cq.answer()
            return

        # Добавляем фото через бизнес-логику
        success, photos_list = await add_telegram_profile_photo(
            session, user, photo
        )

        if not success:
            await session.commit()
            await cq.message.edit_text(
                f"⚠️ Достигнут максимум из {MAX_PHOTOS} фото.",
                reply_markup=kb_profile_photo_clear_save(),
            )
            await cq.answer()
            return

        await cq.message.delete()
        await _send_photos_with_actions(
            cq.bot, cq.message.chat.id, user, state, photos_list
        )
        await cq.answer()


@router.callback_query(F.data == "prof:photo:add")
async def cb_photo_add(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Добавить ➕» — запрашивает ещё фото.
    """
    async with session_factory() as session:
        user = await get_or_create_user(
            session, cq.from_user.id, cq.from_user.username
        )

        if not can_add_photo(user):
            await session.commit()
            await cq.message.edit_text(
                f"⚠️ Достигнут максимум из {MAX_PHOTOS} фото.",
                reply_markup=kb_profile_photo_clear_save(),
            )
            await cq.answer()
            return

        await session.commit()
        await cq.message.delete()
        sent = await cq.message.answer("Пришлите ещё фото:")
        await state.update_data(last_kb_mid=sent.message_id)
        await cq.answer()


@router.callback_query(F.data == "prof:photo:clear")
async def cb_photo_clear(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Очистить 🗑️» — удаляет все фото.
    """
    async with session_factory() as session:
        user = await get_or_create_user(
            session, cq.from_user.id, cq.from_user.username
        )
        await clear_user_photos(session, user)

    try:
        await cq.message.edit_text("Фото очищены.", reply_markup=None)
        await send_photo_request(cq, state, kb_profile_photo())
    except TelegramBadRequest:
        await cq.message.delete()
        await cq.message.answer("Фото очищены")
        await send_photo_request(cq, state, kb_profile_photo())
        await cq.answer()


@router.callback_query(F.data == "prof:photo:save")
async def cb_photo_save(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Сохранить ✅» — сохраняет фото и переходит на следующий этап.

    Проверяет, что добавлено хотя бы одно фото, переводит пользователя на стадию
    profile_bio и запрашивает описание профиля. Если был режим редактирования отдельного
    поля (photo), возвращает в режим просмотра анкеты.
    """
    await cq.message.delete()
    async with session_factory() as session:
        user = await get_or_create_user(
            session, cq.from_user.id, cq.from_user.username
        )

        if not user.photos_json or not user.photos_json.get("photos"):
            await session.commit()
            await cq.message.delete()
            await send_photo_request(cq, state, kb_profile_photo())
            await cq.answer()
            return

        # Проверяем, был ли режим редактирования
        data = await state.get_data()
        editing = data.get("editing_field")
        
        # Проверяем, заполнен ли профиль полностью (для работы после перезапуска бота)
        editing_profile_complete = is_profile_complete(user)
        if editing == "photo" or editing_profile_complete:
            # Возвращаемся в режим просмотра анкеты
            user.stage = "profile_review"
            await session.commit()
            await state.update_data(editing_field=None)
            await _send_profile_preview(
                cq.message.bot,
                cq.message.chat.id,
                user,
                state,
                kb_profile_review(),
                send_photos=False,
            )
        else:
            # Переходим на следующий этап (биография)
            user.stage = "profile_bio"
            await session.commit()
            await cq.message.answer("Расскажите о себе (до 500 символов):")
            await state.update_data(last_kb_mid=None)

        await cq.answer()


@router.callback_query(F.data == "prof:edit:photo")
async def cb_edit_photo(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки «Изменить фото» при редактировании анкеты.
    """
    try:
        async with session_factory() as session:
            user = await get_or_create_user(
                session, cq.from_user.id, cq.from_user.username
            )
            user.stage = "profile_photo"
            user.last_activity = now_utc()
            await session.commit()

            # режим редактирования
            await state.update_data(editing_field="photo")

            photos_list = get_photos_list(user)

            # Превращаем сообщение в клавиатуру без текста
            keyboard = (
                kb_profile_photo_with_photos()
                if photos_list
                else kb_profile_photo()
            )
            await cq.message.edit_text("Изменение фото", reply_markup=keyboard)
            await state.update_data(last_kb_mid=cq.message.message_id)
            await cq.answer()
    except Exception as e:
        print(f"Error in cb_edit_photo: {repr(e)}")
        await cq.answer()
        try:
            await cq.message.answer(
                "❌ Ошибка при загрузке фотографий. Попробуйте ещё раз."
            )
        except:
            pass

