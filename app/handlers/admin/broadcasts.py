"""
Обработчики для системы рассылок в административной панели.

Содержит обработчики для создания, планирования и отправки рассылок пользователям.
Поддерживает немедленную и запланированную отправку с предпросмотром.
Поддерживает медиа-группы (альбомы до 10 фото).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.keyboards.kb_admin import (
    kb_admin_broadcasts,
    kb_admin_broadcast_back,
    kb_admin_broadcast_preview,
    kb_admin_menu,
)
from app.keyboards.utils import clear_last_kb
from app.services.admin import create_broadcast, send_broadcast, get_active_users
from app.handlers.fsm import FSMDataKeys


router = Router()

# Буфер для накопления медиа-групп
_media_group_buffer: Dict[str, List[dict]] = {}
_media_group_tasks: Dict[str, asyncio.Task] = {}


def _restore_html_from_entities(text: str, entities: list) -> str:
    """
    Восстанавливает HTML-форматирование из entities.
    
    Args:
        text: Оригинальный текст
        entities: Список entities из Telegram
    
    Returns:
        str: Текст с HTML-тегами
    """
    if not entities:
        return text
    
    # Сортируем entities по offset (позиции в тексте)
    sorted_entities = sorted(entities, key=lambda e: e.offset, reverse=True)
    
    result = text
    for entity in sorted_entities:
        start = entity.offset
        length = entity.length
        end = start + length
        
        # Извлекаем текст для форматирования
        entity_text = result[start:end]
        
        # Определяем HTML-тег в зависимости от типа entity
        if entity.type == "bold":
            replacement = f"<b>{entity_text}</b>"
        elif entity.type == "italic":
            replacement = f"<i>{entity_text}</i>"
        elif entity.type == "underline":
            replacement = f"<u>{entity_text}</u>"
        elif entity.type == "strikethrough":
            replacement = f"<s>{entity_text}</s>"
        elif entity.type == "code":
            replacement = f"<code>{entity_text}</code>"
        elif entity.type == "pre":
            replacement = f"<pre>{entity_text}</pre>"
        else:
            replacement = entity_text
        
        # Заменяем текст на форматированный
        result = result[:start] + replacement + result[end:]
    
    return result


class BroadcastStates(StatesGroup):
    """Состояния FSM для работы с рассылками."""
    
    waiting_for_schedule_time = State()  # Ожидание ввода времени планирования
    waiting_for_content = State()  # Ожидание ввода контента рассылки
    preview = State()  # Показ предпросмотра


@router.callback_query(F.data == "admin:broadcasts")
async def show_broadcasts_menu(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Отображает меню рассылок с выбором типа отправки.
    
    Args:
        callback: Callback query от нажатия кнопки "Рассылки"
        state: FSM контекст
    """
    await callback.answer()
    
    # Очищаем предыдущее состояние рассылки
    data = await state.get_data()
    broadcast_data = {k: v for k, v in data.items() if not k.startswith("broadcast_")}
    await state.set_data(broadcast_data)
    
    await callback.message.edit_text(
        "Как отправить рассылку?",
        reply_markup=kb_admin_broadcasts(),
    )


@router.callback_query(F.data == "admin:broadcast:send_now")
async def broadcast_send_now(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Начинает процесс создания немедленной рассылки.
    
    Args:
        callback: Callback query
        state: FSM контекст
    """
    await callback.answer()
    
    # Сохраняем, что это немедленная рассылка
    await state.update_data(broadcast_is_scheduled=False)
    await state.set_state(BroadcastStates.waiting_for_content)
    
    await callback.message.edit_text(
        "📝 <b>Создание рассылки</b>\n\n"
        "Отправьте материалы для рассылки:\n"
        "• Текст\n"
        "• Фото с подписью\n\n"
        "После отправки материалов вы увидите предпросмотр.",
        reply_markup=kb_admin_broadcast_back(),
    )


@router.callback_query(F.data == "admin:broadcast:schedule")
async def broadcast_schedule(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Начинает процесс создания запланированной рассылки.
    
    Args:
        callback: Callback query
        state: FSM контекст
    """
    await callback.answer()
    
    # Сохраняем, что это запланированная рассылка
    await state.update_data(broadcast_is_scheduled=True)
    await state.set_state(BroadcastStates.waiting_for_schedule_time)
    
    await callback.message.edit_text(
        "📅 <b>Запланировать рассылку</b>\n\n"
        "Введите дату и время отправки в формате:\n"
        "<code>день.месяц.год часы:минуты</code>\n\n"
        "Пример: <code>25.01.2026 15:30</code>",
        reply_markup=kb_admin_broadcast_back(),
    )


@router.callback_query(F.data == "admin:broadcast:back")
async def broadcast_back(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Возвращает пользователя назад к меню рассылок.
    
    Args:
        callback: Callback query
        state: FSM контекст
    """
    await callback.answer()
    
    # Очищаем состояние
    await state.clear()
    
    # Восстанавливаем флаг админ-панели
    await state.update_data(**{FSMDataKeys.ADMIN_PANEL_ACTIVE: True})
    
    # Возвращаем в меню рассылок
    await show_broadcasts_menu(callback, state)


@router.message(BroadcastStates.waiting_for_schedule_time)
async def process_schedule_time(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Обрабатывает ввод даты и времени для запланированной рассылки.
    
    Args:
        message: Сообщение с датой и временем
        state: FSM контекст
    """
    # Удаляем предыдущую клавиатуру
    await clear_last_kb(state, message.chat.id, message.bot)
    
    try:
        # Парсим дату и время
        scheduled_dt = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        
        # Проверяем, что время в будущем
        now = datetime.now()
        if scheduled_dt <= now:
            sent = await message.answer(
                "❌ <b>Ошибка</b>\n\n"
                "Время отправки должно быть в будущем.\n"
                "Попробуйте снова или вернитесь назад.",
                reply_markup=kb_admin_broadcast_back(),
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
            return
        
        # Сохраняем время
        await state.update_data(broadcast_scheduled_at=scheduled_dt.isoformat())
        
        # Переходим к вводу контента
        await state.set_state(BroadcastStates.waiting_for_content)
        
        sent = await message.answer(
            f"✅ Рассылка запланирована на: <b>{scheduled_dt.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            "📝 Теперь отправьте материалы для рассылки:\n"
            "• Текст\n"
            "• Фото с подписью\n\n"
            "После отправки материалов вы увидите предпросмотр.",
            reply_markup=kb_admin_broadcast_back(),
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
        
    except ValueError:
        sent = await message.answer(
            "❌ <b>Ошибка формата</b>\n\n"
            "Неверный формат даты и времени.\n"
            "Используйте формат: <code>день.месяц.год часы:минуты</code>\n\n"
            "Пример: <code>25.01.2026 15:30</code>",
            reply_markup=kb_admin_broadcast_back(),
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})


@router.message(BroadcastStates.waiting_for_content, F.text)
async def process_text_content(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Обрабатывает текстовый контент для рассылки.
    
    Args:
        message: Сообщение с текстом
        state: FSM контекст
    """
    # Удаляем предыдущую клавиатуру
    await clear_last_kb(state, message.chat.id, message.bot)
    
    # Сохраняем текст
    await state.update_data(
        broadcast_text=message.text,
        broadcast_html=message.html_text,
    )
    
    # Переходим к предпросмотру
    await show_broadcast_preview(message, state)


@router.message(BroadcastStates.waiting_for_content, F.photo & F.media_group_id)
async def process_photo_group_content(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Обрабатывает фото из медиа-группы (альбома) для рассылки.
    
    Собирает все медиа из группы и сохраняет как медиа-группу.
    
    Args:
        message: Сообщение с фото из группы
        state: FSM контекст
    """
    await _process_media_group_item(message, state, "photo", message.photo[-1].file_id)




async def _process_media_group_item(
    message: Message,
    state: FSMContext,
    media_type: str,
    file_id: str,
) -> None:
    """
    Общий обработчик для элементов медиа-группы.
    
    Args:
        message: Сообщение с медиа
        state: FSM контекст
        media_type: Тип медиа (photo)
        file_id: ID файла
    """
    media_group_id = str(message.media_group_id)
    user_id = message.from_user.id
    key = f"{user_id}_{media_group_id}"
    
    # Добавляем медиа в буфер
    if key not in _media_group_buffer:
        _media_group_buffer[key] = []
    
    # Получаем caption и HTML версию
    caption = message.caption or ""
    caption_html = ""
    
    # Пытаемся получить HTML версию caption
    if message.caption:
        # Пробуем разные способы получить HTML версию
        if hasattr(message, 'caption_html') and message.caption_html:
            caption_html = message.caption_html
        elif hasattr(message, 'html_caption') and message.html_caption:
            caption_html = message.html_caption
        # Если есть entities, восстанавливаем HTML из них
        elif message.caption_entities:
            try:
                from aiogram.utils.text_decorations import html_decoration
                caption_html = html_decoration.unparse(message.caption, message.caption_entities)
            except (ImportError, AttributeError):
                # Если не получилось, пробуем вручную восстановить форматирование
                caption_html = _restore_html_from_entities(message.caption, message.caption_entities)
        else:
            # Fallback на обычный caption
            caption_html = message.caption
    
    # Сохраняем медиа с метаданными
    _media_group_buffer[key].append({
        "type": media_type,
        "file_id": file_id,
        "caption": caption,
        "html_text": caption_html,
    })
    
    # Если задача уже запущена, просто выходим
    if key in _media_group_tasks:
        return
    
    # Запускаем задачу для финализации медиа-группы
    task = asyncio.create_task(
        _finalize_media_group(message, state, key)
    )
    _media_group_tasks[key] = task


async def _finalize_media_group(
    message: Message,
    state: FSMContext,
    key: str,
) -> None:
    """
    Финализирует медиа-группу после небольшой задержки.
    
    Args:
        message: Исходное сообщение
        state: FSM контекст
        key: Ключ медиа-группы
    """
    try:
        # Ждём, пока все сообщения из группы придут
        await asyncio.sleep(0.7)
        
        # Получаем все медиа из буфера
        media_items = _media_group_buffer.get(key, [])
        if not media_items:
            return
        
        # Удаляем предыдущую клавиатуру
        await clear_last_kb(state, message.chat.id, message.bot)
        
        # Берём caption из первого элемента
        # Проверяем все элементы и берём первый непустой caption
        caption_text = ""
        caption_html = ""
        
        for item in media_items:
            caption = item.get("caption", "")
            html = item.get("html_text", "")
            
            if caption:  # Если есть непустой caption
                caption_text = caption
                # Используем HTML версию если она есть, иначе обычный caption
                caption_html = html if html else caption
                break
        
        # Если caption_html всё ещё пустой, используем caption_text
        if not caption_html and caption_text:
            caption_html = caption_text
        
        # Сохраняем данные медиа-группы
        await state.update_data(
            broadcast_text=caption_text,
            broadcast_html=caption_html,
            broadcast_media_type="media_group",
            broadcast_media_items=[
                {"type": item["type"], "file_id": item["file_id"]}
                for item in media_items
            ],
        )
        
        # Переходим к предпросмотру
        await show_broadcast_preview(message, state)
        
        # Очищаем буфер
        if key in _media_group_buffer:
            del _media_group_buffer[key]
    
    finally:
        # Удаляем задачу из списка
        if key in _media_group_tasks:
            del _media_group_tasks[key]


@router.message(BroadcastStates.waiting_for_content, F.photo & ~F.media_group_id)
async def process_photo_content(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Обрабатывает одиночное фото для рассылки.
    
    Args:
        message: Сообщение с фото
        state: FSM контекст
    """
    # Удаляем предыдущую клавиатуру
    await clear_last_kb(state, message.chat.id, message.bot)
    
    # Берём самое большое фото
    photo = message.photo[-1]
    
    # Получаем caption и HTML версию (та же логика, что и для медиа-групп)
    caption = message.caption or ""
    caption_html = ""
    
    # Пытаемся получить HTML версию caption
    if message.caption:
        # Пробуем разные способы получить HTML версию
        if hasattr(message, 'caption_html') and message.caption_html:
            caption_html = message.caption_html
        elif hasattr(message, 'html_caption') and message.html_caption:
            caption_html = message.html_caption
        # Если есть entities, восстанавливаем HTML из них
        elif message.caption_entities:
            try:
                from aiogram.utils.text_decorations import html_decoration
                caption_html = html_decoration.unparse(message.caption, message.caption_entities)
            except (ImportError, AttributeError):
                # Если не получилось, пробуем вручную восстановить форматирование
                caption_html = _restore_html_from_entities(message.caption, message.caption_entities)
        else:
            # Fallback на обычный caption
            caption_html = message.caption
    
    # Сохраняем данные
    await state.update_data(
        broadcast_text=caption,
        broadcast_html=caption_html,
        broadcast_media_type="photo",
        broadcast_media_file_id=photo.file_id,
    )
    
    # Переходим к предпросмотру
    await show_broadcast_preview(message, state)




async def show_broadcast_preview(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Показывает предпросмотр рассылки.
    
    Args:
        message: Исходное сообщение
        state: FSM контекст
    """
    data = await state.get_data()
    
    is_scheduled = data.get("broadcast_is_scheduled", False)
    scheduled_at_iso = data.get("broadcast_scheduled_at")
    text = data.get("broadcast_html", data.get("broadcast_text", ""))
    media_type = data.get("broadcast_media_type")
    media_file_id = data.get("broadcast_media_file_id")
    media_items = data.get("broadcast_media_items", [])
    
    # Формируем заголовок предпросмотра
    preview_header = "📋 <b>Предпросмотр рассылки</b>\n\n"
    
    if is_scheduled and scheduled_at_iso:
        scheduled_dt = datetime.fromisoformat(scheduled_at_iso)
        preview_header += f"📅 Запланирована на: <b>{scheduled_dt.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
    
    # Отправляем предпросмотр
    if media_type == "media_group":
        # Медиа-группа (альбом фото)
        from aiogram.types import InputMediaPhoto
        
        media_group = []
        for idx, item in enumerate(media_items):
            item_type = item.get("type")
            file_id = item.get("file_id")
            
            # Первый элемент получает caption с заголовком и текстом
            caption = (preview_header + text) if idx == 0 else None
            
            if item_type == "photo":
                media_group.append(
                    InputMediaPhoto(
                        media=file_id,
                        caption=caption,
                        parse_mode="HTML" if caption else None,
                    )
                )
        
        # Отправляем медиа-группу
        if media_group:
            await message.bot.send_media_group(
                chat_id=message.chat.id,
                media=media_group,
            )
            # Отправляем кнопки отдельным сообщением
            sent = await message.answer(
                "Выберите действие:",
                reply_markup=kb_admin_broadcast_preview(is_scheduled),
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
    elif media_type == "photo":
        sent = await message.answer_photo(
            photo=media_file_id,
            caption=preview_header + (text if text else "(без подписи)"),
            parse_mode="HTML",
            reply_markup=kb_admin_broadcast_preview(is_scheduled),
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
    else:
        # Только текст
        sent = await message.answer(
            preview_header + text,
            parse_mode="HTML",
            reply_markup=kb_admin_broadcast_preview(is_scheduled),
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
    
    await state.set_state(BroadcastStates.preview)


@router.callback_query(BroadcastStates.preview, F.data == "admin:broadcast:cancel_preview")
async def cancel_preview(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Отменяет предпросмотр и возвращает к вводу контента.
    
    Args:
        callback: Callback query
        state: FSM контекст
    """
    await callback.answer()
    
    # Удаляем предпросмотр
    await callback.message.delete()
    
    # Возвращаемся к вводу контента
    await state.set_state(BroadcastStates.waiting_for_content)
    
    data = await state.get_data()
    is_scheduled = data.get("broadcast_is_scheduled", False)
    scheduled_at_iso = data.get("broadcast_scheduled_at")
    
    text = "📝 <b>Создание рассылки</b>\n\n"
    
    if is_scheduled and scheduled_at_iso:
        scheduled_dt = datetime.fromisoformat(scheduled_at_iso)
        text += f"📅 Запланирована на: <b>{scheduled_dt.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
    
    text += (
        "Отправьте материалы для рассылки:\n"
        "• Текст\n"
        "• Фото с подписью"
    )
    
    sent = await callback.message.answer(
        text,
        reply_markup=kb_admin_broadcast_back(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})


@router.callback_query(BroadcastStates.preview, F.data == "admin:broadcast:confirm_send")
async def confirm_send_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Подтверждает и отправляет рассылку.
    
    Args:
        callback: Callback query
        state: FSM контекст
        session_factory: Фабрика сессий БД
    """
    await callback.answer("Создаю рассылку...")
    
    data = await state.get_data()
    
    is_scheduled = data.get("broadcast_is_scheduled", False)
    scheduled_at_iso = data.get("broadcast_scheduled_at")
    # Используем HTML версию текста с fallback на обычный текст
    text = data.get("broadcast_html", data.get("broadcast_text", ""))
    media_type = data.get("broadcast_media_type")
    media_file_id = data.get("broadcast_media_file_id")
    media_items = data.get("broadcast_media_items", [])
    
    # Формируем JSON для медиа
    media_json = None
    if media_type == "media_group" and media_items:
        # Медиа-группа
        media_json = {
            "type": "media_group",
            "items": media_items,
        }
    elif media_type and media_file_id:
        # Одиночное медиа
        media_json = {
            "type": media_type,
            "file_id": media_file_id,
        }
    
    # Парсим дату если запланирована
    scheduled_at = None
    if is_scheduled and scheduled_at_iso:
        scheduled_at = datetime.fromisoformat(scheduled_at_iso)
    
    async with session_factory() as session:
        # Создаём рассылку в БД
        broadcast = await create_broadcast(
            session=session,
            admin_id=callback.from_user.id,
            message_text=text if text else None,
            media_json=media_json,
            scheduled_at=scheduled_at,
        )
        
        # Если немедленная отправка - отправляем сразу
        if not is_scheduled:
            # Получаем количество пользователей
            users = await get_active_users(session)
            total_users = len(users)
            
            # Обновляем сообщение (проверяем, можно ли редактировать)
            status_text = (
                f"📨 <b>Отправка рассылки...</b>\n\n"
                f"Всего пользователей: {total_users}\n"
                f"Отправлено: 0 / {total_users}\n\n"
                f"⏳ Пожалуйста, подождите. Это может занять некоторое время."
            )
            
            # Если сообщение с медиа - удаляем и отправляем новое, иначе редактируем
            if callback.message.photo:
                await callback.message.delete()
                status_msg = await callback.message.answer(status_text)
                status_message_id = status_msg.message_id
            else:
                await callback.message.edit_text(status_text)
                status_message_id = callback.message.message_id
            
            # Запускаем отправку в фоне
            asyncio.create_task(
                send_broadcast_task(
                    callback.message.chat.id,
                    status_message_id,
                    callback.bot,
                    session_factory,
                    broadcast.id,
                )
            )
        else:
            # Запланированная рассылка
            success_text = (
                f"✅ <b>Рассылка запланирована</b>\n\n"
                f"📅 Время отправки: <b>{scheduled_at.strftime('%d.%m.%Y %H:%M')}</b>\n"
                f"🆔 ID рассылки: <code>{broadcast.id}</code>\n\n"
                f"Рассылка будет отправлена автоматически в указанное время."
            )
            
            # Если сообщение с медиа - удаляем и отправляем новое, иначе редактируем
            if callback.message.photo:
                await callback.message.delete()
                await callback.message.answer(success_text, reply_markup=kb_admin_menu())
            else:
                await callback.message.edit_text(success_text, reply_markup=kb_admin_menu())
    
    # Очищаем состояние
    await state.clear()
    await state.update_data(**{FSMDataKeys.ADMIN_PANEL_ACTIVE: True})


async def send_broadcast_task(
    chat_id: int,
    message_id: int,
    bot,
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: int,
) -> None:
    """
    Фоновая задача для отправки рассылки с обновлением прогресса.
    
    Args:
        chat_id: ID чата для обновления сообщения
        message_id: ID сообщения для обновления
        bot: Экземпляр бота
        session_factory: Фабрика сессий БД
        broadcast_id: ID рассылки
    """
    async with session_factory() as session:
        try:
            # Отправляем рассылку
            sent_count, failed_count = await send_broadcast(
                bot=bot,
                session=session,
                broadcast_id=broadcast_id,
                rate_limit_delay=0.05,  # 50ms между сообщениями
            )
            
            # Обновляем сообщение с результатами
            result_text = (
                f"✅ <b>Рассылка завершена</b>\n\n"
                f"📊 Статистика:\n"
                f"• Отправлено: {sent_count}\n"
                f"• Ошибок: {failed_count}\n"
                f"• Всего: {sent_count + failed_count}"
            )
            
            try:
                # Пытаемся отредактировать сообщение
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=result_text,
                    reply_markup=kb_admin_menu(),
                )
            except Exception:
                # Если не получилось (медиа-сообщение), удаляем и отправляем новое
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:
                    pass  # Игнорируем ошибку удаления
                await bot.send_message(
                    chat_id=chat_id,
                    text=result_text,
                    reply_markup=kb_admin_menu(),
                )
            
        except Exception as e:
            # Обработка ошибок
            error_text = (
                f"❌ <b>Ошибка при отправке рассылки</b>\n\n"
                f"Произошла ошибка: {str(e)}"
            )
            
            try:
                # Пытаемся отредактировать сообщение
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=error_text,
                    reply_markup=kb_admin_menu(),
                )
            except Exception:
                # Если не получилось (медиа-сообщение), удаляем и отправляем новое
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:
                    pass  # Игнорируем ошибку удаления
                await bot.send_message(
                    chat_id=chat_id,
                    text=error_text,
                    reply_markup=kb_admin_menu(),
                )
