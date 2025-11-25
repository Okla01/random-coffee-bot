"""
Middleware для внедрения фабрики сессий БД в контекст обработчиков.

Предоставляет session_factory каждому обработчику через параметры data,
позволяя обработчикам легко получить асинхронную сессию для работы с БД.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


class DbSessionMiddleware(BaseMiddleware):
    """
    Middleware для внедрения фабрики сессий БД.

    Добавляет session_factory в параметры data каждого обновления (update),
    позволяя обработчикам получить доступ к фабрике для создания сессий БД.
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
        Обрабатывает обновление и передаёт session_factory в данные.

        Args:
            handler: следующий обработчик в цепи.
            event (TelegramObject): объект события (сообщение, callback и т.д.).
            data (Dict[str, Any]): словарь параметров обработчика.

        Returns:
            Any: результат выполнения handler.
        """
        data["session_factory"] = self._factory
        return await handler(event, data)
