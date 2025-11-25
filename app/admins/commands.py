"""
Административная панель для управления пользователями и рассмотрением блокировок.

Предоставляет доступ администраторам (по ролям или ADMIN_IDS) к утилитам управления.
Обрабатывает блокировку/разблокировку пользователей с логированием всех действий.
Синхронизирует роли из конфигурации с БД.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import Settings
from app.core.users import get_user_by_tg_id
from app.database import User, Role, UserRole, AdminLog

router = Router()


async def _sync_admin_role(
    session: AsyncSession, settings: Settings, user: User
) -> None:
    """
    Синхронизирует роль администратора для пользователя.

    Если пользователь указан в ADMIN_IDS конфигурации, добавляет ему роль 'admin'.
    Создаёт роль при необходимости. Не удаляет роль если пользователь исключён из ADMIN_IDS.

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        user (User): объект пользователя.

    Returns:
        None: ничего не возвращает.
    """
    if user.telegram_id not in settings.admin_ids:
        return

    # Получаем или создаём роль admin
    role = (
        await session.execute(select(Role).where(Role.name == "admin"))
    ).scalar_one_or_none()
    if not role:
        role = Role(name="admin")
        session.add(role)
        await session.flush()

    # Проверяем, есть ли уже связь пользователь-роль
    link = (
        await session.execute(
            select(UserRole).where(
                UserRole.user_id == user.id, UserRole.role_id == role.id
            )
        )
    ).scalar_one_or_none()
    if not link:
        session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.flush()


async def _is_admin(session: AsyncSession, settings: Settings, tg_id: int) -> bool:
    """
    Проверяет, является ли пользователь администратором.

    Пользователь считается администратором, если:
    - его Telegram ID находится в ADMIN_IDS конфигурации, или
    - у него есть роль 'admin' в БД.

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        tg_id (int): Telegram ID пользователя для проверки.

    Returns:
        bool: True если пользователь администратор, иначе False.
    """
    # Проверяем в ADMIN_IDS конфигурации
    if tg_id in settings.admin_ids:
        return True

    # Проверяем роль в БД
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return False

    q = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id, Role.name == "admin")
    )
    return (await session.execute(q)).scalar_one_or_none() is not None


@router.message(Command("admin"))
async def cmd_admin(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает команду /admin — открывает административную панель.

    Проверяет права администратора (по ADMIN_IDS или роли), создаёт пользователя
    если его нет в БД, логирует открытие панели и отправляет приветственное сообщение.
    Отказывает в доступе если пользователь заблокирован.

    Args:
        message (Message): объект сообщения /admin.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            # создаём, если tg_id ∈ ADMIN_IDS (ТЗ 8.3)
            if message.from_user.id in settings.admin_ids:
                user = User(
                    telegram_id=message.from_user.id,
                    username=message.from_user.username,
                    status="new",
                    stage="new",
                )
                session.add(user)
                await session.flush()
            else:
                await message.answer("⛔️ Нет прав.")
                return

        # Синхронизируем роль администратора если пользователь в ADMIN_IDS
        await _sync_admin_role(session, settings, user)

        if user.status == "blocked":
            await message.answer("⛔️ Нет прав (пользователь заблокирован).")
            return

        if not await _is_admin(session, settings, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return

        user.last_activity = datetime.now(timezone.utc)
        session.add(
            AdminLog(
                admin_telegram_id=message.from_user.id,
                action="open_admin",
                payload={"user_id": user.id},
            )
        )
        await session.commit()

        await message.answer(
            "Админ-панель открыта.\nДействия по заявкам будут приходить в админ-чат при блокировках."
        )


@router.callback_query()
async def admin_callbacks(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает callback-запросы для блокировки/разблокировки пользователей.

    Интерпретирует callback data формата 'admin:block:ID' или 'admin:unblock:ID'.
    Изменяет статус пользователя, уведомляет его о решении, логирует действие
    и обновляет исходное сообщение с указанием администратора.
    Отказывает в доступе если caller не администратор.
    """
    data = cq.data or ""
    if not (data.startswith("admin:block:") or data.startswith("admin:unblock:")):
        return

    async with session_factory() as session:
        # Проверка прав администратора
        if not await _is_admin(session, settings, cq.from_user.id):
            await cq.answer("Нет прав")
            return

        _, action, user_id_str = data.split(":")
        target_id = int(user_id_str)

        user = (
            await session.execute(select(User).where(User.id == target_id))
        ).scalar_one_or_none()

        if not user:
            await cq.answer("Пользователь не найден")
            return

        reviewed_by = cq.from_user.username or str(cq.from_user.id)

        if action == "block":
            # Блокировка пользователя
            user.status = "blocked"
            session.add(
                AdminLog(
                    admin_telegram_id=cq.from_user.id,
                    action="block",
                    payload={"user_id": user.id},
                )
            )
            await session.commit()

            # Обновляем исходное сообщение: дописываем решение и убираем inline-клавиатуру
            await cq.message.edit_text(
                cq.message.text
                + f"\n\nРешение: Пользователь {'@' + user.username} заблокирован."
                  f"\n👨‍💻Рассмотрел: {'@' + reviewed_by}",
                reply_markup=None,
            )

            # Пытаемся уведомить пользователя
            if user.telegram_id:
                try:
                    await cq.message.bot.send_message(
                        user.telegram_id,
                        (
                            "Решение по временной блокировке: Вам закрыт доступ. "
                            "Если считаете это ошибкой — обратитесь к администратору."
                        ),
                    )
                except Exception:
                    # Здесь можно залогировать, если используешь логгер
                    pass

        else:
            # Разблокировка: сбрасываем статус и счётчики
            user.status = "new"
            user.stage = "verifying_email"
            user.email_attempts = 0
            user.otp_attempts = 0

            session.add(
                AdminLog(
                    admin_telegram_id=cq.from_user.id,
                    action="unblock",
                    payload={"user_id": user.id},
                )
            )
            await session.commit()

            # Обновляем исходное сообщение: дописываем решение и убираем inline-клавиатуру
            await cq.message.edit_text(
                cq.message.text
                + f"\n\nРешение: Пользователь {'@' + user.username} разблокирован "
                  f"и возвращён к вводу корпоративного e-mail."
                  f"\n👨‍💻Рассмотрел: {'@' + reviewed_by}",
                reply_markup=None,
            )

            # Уведомляем пользователя
            if user.telegram_id:
                try:
                    await cq.message.bot.send_message(
                        user.telegram_id,
                        (
                            "Решение по временной блокировке: Вас разблокировали. "
                            "Пожалуйста, пройдите регистрацию заново.\n"
                            "Введите корпоративный e-mail:"
                        ),
                    )
                except Exception:
                    # Здесь тоже можно залогировать
                    pass

        # Закрываем "часы" у колбэка
        await cq.answer()