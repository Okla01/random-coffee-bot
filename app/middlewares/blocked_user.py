"""
Middleware для проверки блокировки пользователя.

Перехватывает все обновления (Message, CallbackQuery) и проверяет статус пользователя.
Если пользователь заблокирован, отправляет уведомление и прерывает обработку.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery, Update, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.database.db import get_user_by_tg_id, is_user_blocked
from app.services.admin.roles import is_admin
from app.services.core import Settings


class BlockedUserMiddleware(BaseMiddleware):
    """
    Middleware для проверки блокировки пользователя.
    
    Проверяет статус пользователя перед обработкой любого обновления.
    Если пользователь заблокирован, отправляет уведомление и прерывает цепочку обработки.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """
        Инициализирует middleware с фабрикой сессий.
        
        Args:
            session_factory (async_sessionmaker[AsyncSession]): фабрика асинхронных сессий БД.
        """
        super().__init__()
        self._factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """
        Проверяет блокировку пользователя перед обработкой любого обновления.
        
        Любое действие пользователя приводит к проверке блокировки. Если пользователь
        заблокирован, отправляется сообщение о блокировке и удаляется последняя клавиатура.
        
        Args:
            handler: следующий обработчик в цепи.
            event (TelegramObject): объект события (сообщение, callback и т.д.).
            data (Dict[str, Any]): словарь параметров обработчика.
        
        Returns:
            Any: результат выполнения handler или None если пользователь заблокирован.
        """
        # Извлекаем событие из Update
        target = None
        if isinstance(event, Update):
            target = (
                event.callback_query
                or event.message
                or event.edited_message
                or event.channel_post
                or event.edited_channel_post
            )
        elif isinstance(event, (Message, CallbackQuery)):
            target = event
        
        # Если нет события или нет пользователя - пропускаем
        if not target or not hasattr(target, "from_user") or not target.from_user:
            return await handler(event, data)
        
        # Получаем пользователя из БД и проверяем блокировку
        telegram_id = target.from_user.id
        
        async with self._factory() as session:
            user = await get_user_by_tg_id(session, telegram_id)
            
            # Если пользователь не найден - пропускаем
            if not user:
                return await handler(event, data)
            
            # Проверяем блокировку
            if is_user_blocked(user):
                message_text = "Доступ временно заблокирован. Свяжитесь с администратором."
                
                # Получаем settings для проверки админских прав
                settings = data.get("settings")
                if settings is None:
                    dispatcher = data.get("dispatcher")
                    if dispatcher and "settings" in dispatcher:
                        settings = dispatcher["settings"]
                    else:
                        settings = Settings.load()
                
                # Проверяем, является ли пользователь админом
                user_is_admin = await is_admin(session, settings, telegram_id)
                
                if isinstance(target, CallbackQuery):
                    # Для callback'ов - всплывающее уведомление
                    await target.answer(message_text, show_alert=True)
                    
                    # Для обычных пользователей удаляем клавиатуру
                    if not user_is_admin:
                        try:
                            await target.message.edit_reply_markup(reply_markup=None)
                        except Exception:
                            pass
                elif isinstance(target, Message):
                    # Для сообщений - отправляем текстовое сообщение
                    # Для обычных пользователей удаляем клавиатуру
                    await target.answer(
                        message_text,
                        reply_markup=ReplyKeyboardRemove() if not user_is_admin else None
                    )
                
                return  # Прерываем обработку
        
        # Пользователь не заблокирован - продолжаем обработку
        return await handler(event, data)

