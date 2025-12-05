"""
Middleware для внедрения планировщика матчинга в контекст обработчиков.

Предоставляет matching_scheduler каждому обработчику через параметры data,
позволяя обработчикам получить доступ к планировщику для обновления расписания.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler


class SchedulerMiddleware(BaseMiddleware):
    """
    Middleware для внедрения планировщика матчинга.

    Добавляет matching_scheduler в параметры data каждого обновления (update),
    позволяя обработчикам получить доступ к планировщику для управления джобами.
    """

    def __init__(self, scheduler: AsyncIOScheduler) -> None:
        """
        Инициализирует middleware с планировщиком.

        Args:
            scheduler (AsyncIOScheduler): планировщик задач матчинга.
        """
        super().__init__()
        self._scheduler = scheduler

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """
        Обрабатывает обновление и передаёт matching_scheduler в данные.

        Args:
            handler: следующий обработчик в цепи.
            event (TelegramObject): объект события (сообщение, callback и т.д.).
            data (Dict[str, Any]): словарь параметров обработчика.

        Returns:
            Any: результат выполнения handler.
        """
        data["matching_scheduler"] = self._scheduler
        return await handler(event, data)
