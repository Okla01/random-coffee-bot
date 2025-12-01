"""
Inline-обработчик для поиска пользователей по username.

Администраторы могут искать пользователей через inline-режим бота,
вводя @username или часть имени пользователя.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.db import search_users_by_username
from app.services.admin.users import get_user_roles
from app.services.const import ROLE_NAMES, USER_STATUS_NAMES

router = Router()


@router.inline_query(F.query.startswith("user:"))
async def inline_search_users(
    query: InlineQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает inline-запросы для поиска пользователей по username.

    Формат запроса: user:@username или user:username
    """
    # Извлекаем поисковый запрос после "user:"
    search_query = query.query[5:].strip()  # убираем "user:"

    if not search_query:
        await query.answer(
            results=[],
            cache_time=1,
            is_personal=True,
        )
        return

    async with session_factory() as session:
        users = await search_users_by_username(session, search_query, limit=10)

        results: list[InlineQueryResultArticle] = []

        for user in users:
            # Получаем роли пользователя
            roles = await get_user_roles(session, user.id)
            roles_str = ", ".join(
                ROLE_NAMES.get(r.name, r.name) for r in roles
            ) if roles else "нет"
            status_str = USER_STATUS_NAMES.get(user.status, user.status)

            # Формируем текст профиля
            text_lines = [
                "👤 <b>Профиль пользователя</b>",
                "",
                f"Telegram ID: <code>{user.telegram_id}</code>",
                f"Username: @{user.username}" if user.username else "Username: —",
                "",
                f"Статус: {status_str}",
                f"Роли: {roles_str}",
                "",
                f"Имя в анкете: {user.name or '—'}",
                f"Возраст: {user.age or '—'}",
                f"Описание: {user.bio or '—'}",
                "",
                f"Зарегистрирован: {user.registered_at:%d.%m.%Y %H:%M}",
                f"Последняя активность: {user.last_activity:%d.%m.%Y %H:%M}",
            ]
            profile_text = "\n".join(text_lines)

            # Описание для превью
            description = f"{status_str} | Роли: {roles_str}"

            results.append(
                InlineQueryResultArticle(
                    id=str(user.id),
                    title=f"@{user.username}" if user.username else f"ID: {user.telegram_id}",
                    description=description,
                    input_message_content=InputTextMessageContent(
                        message_text=profile_text,
                        parse_mode="HTML",
                    ),
                )
            )

        await query.answer(
            results=results,
            cache_time=1,  # Минимальный кеш для актуальных данных
            is_personal=True,  # Результаты персональные для каждого пользователя
        )

