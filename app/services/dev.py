"""
Dev команды для отладки и тестирования.

Все команды доступны только администраторам.
"""

from __future__ import annotations

from datetime import timedelta

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Match, User
from app.database.utils import now_msk
from app.keyboards.utils import clear_last_kb
from app.services.admin import is_admin
from app.services.admin.complaints import submit_complaint
from app.services.const import USER_STATUS_NOT_ACTIVE
from app.services.core import Settings
from app.services.matching import run_matching_round
from app.services.matching.feedback import send_feedback_to_users
from app.services.matching.jobs import (
    process_match_timeouts_only,
    process_match_reminders_only,
)
from app.services.matching.settings import load_matching_settings
from app.database.db import get_or_create_user

router = Router()


@router.message(Command("test_complaint"))
async def cmd_test_complaint(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    """
    Тестовая команда для создания жалобы.

    Формат: /test_complaint <reported_tg_id> <текст жалобы...>

    Создаёт жалобу от имени отправителя на указанного пользователя.
    """
    async with session_factory() as session:
        # Проверяем права администратора
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            return

        # Используем admin_chat_id_complaints, если задан, иначе fallback на admin_chat_id
        complaints_chat_id = settings.admin_chat_id_complaints or settings.admin_chat_id
        if not complaints_chat_id:
            await message.answer("❌ ADMIN_CHAT_ID_COMPLAINTS или ADMIN_CHAT_ID не настроен.")
            return

        # Парсим аргументы команды
        args = message.text.split(maxsplit=2)
        if len(args) < 3:
            await message.answer(
                "❌ Использование: /test_complaint <reported_tg_id> <текст жалобы...>\n"
                "Пример: /test_complaint 123456789 Этот пользователь не пришёл на встречу"
            )
            return

        try:
            reported_tg_id = int(args[1])
        except ValueError:
            await message.answer("❌ reported_tg_id должен быть числом.")
            return

        complaint_text = args[2]

        # Проверяем, что reported существует в БД
        reported_user = (
            await session.execute(
                select(User).where(User.telegram_id == reported_tg_id)
            )
        ).scalar_one_or_none()

        if not reported_user:
            await message.answer(
                f"❌ Пользователь с tg_id {reported_tg_id} не найден в БД."
            )
            return

        try:
            complaint = await submit_complaint(
                session=session,
                bot=bot,
                admin_chat_id=complaints_chat_id,
                reporter_user_id=message.from_user.id,
                reported_user_id=reported_tg_id,
                complaint_text=complaint_text,
            )
            await message.answer(
                f"✅ Тестовая жалоба #{complaint.id} создана и отправлена в админ-чат."
            )
        except ValueError as e:
            await message.answer(f"❌ Ошибка: {e}")
        except Exception as e:
            await message.answer(f"❌ Не удалось создать жалобу: {e}")


@router.message(Command("test_matching"))
async def cmd_test_matching(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Принудительно запускает раунд мэтчинга (доступно только администраторам).
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Запускаю тестовый раунд мэтчинга...")

    async with session_factory() as session:
        await run_matching_round(session, message.bot)

    await message.answer("✅ Раунд мэтчинга завершён.")


@router.message(Command("reset_matching"))
async def cmd_reset_matching(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Очищает все записи в таблице matches и сбрасывает last_pairing_at у всех пользователей.

    Доступно только администраторам. Используется для полного сброса состояния мэтчинга.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Очищаю все мэтчи и сбрасываю last_pairing_at...")

    async with session_factory() as session:
        # Удаляем все записи из matches
        await session.execute(delete(Match))
        # Очищаем last_pairing_at и last_match_at у всех пользователей
        await session.execute(
            update(User).values(last_pairing_at=None, last_match_at=None)
        )

        await session.commit()

    await message.answer(
        "✅ Все мэтчи удалены, last_pairing_at сброшен у всех пользователей."
    )


@router.message(Command("test_scheduler"))
async def cmd_test_scheduler(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Немедленно запускает джобу мэтчинга из планировщика (доступно только администраторам).

    Полезно для тестирования работы планировщика без ожидания наступления времени.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Запускаю джобу мэтчинга из планировщика...")

    from app.services.matching.scheduler import _matching_round_job

    await _matching_round_job(session_factory, message.bot)
    await message.answer("✅ Джоба выполнена.")


@router.message(Command("test_timeouts"))
async def cmd_test_timeouts(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Принудительно проверяет таймауты для мэтчей в активных статусах (доступно только администраторам).

    Проверяет все мэтчи с активными статусами и переводит их в expired_timeout,
    если истёк таймаут ответа.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Проверяю таймауты для активных мэтчей...")

    async with session_factory() as session:
        matching_settings = await load_matching_settings(session)
        expired_count = await process_match_timeouts_only(
            session, matching_settings, message.bot
        )

    await message.answer(
        f"✅ Проверка таймаутов завершена. Истёкших мэтчей: {expired_count}"
    )


@router.message(Command("test_reminder"))
async def cmd_test_reminder(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Принудительно проверяет и отправляет напоминания для мэтчей в активных статусах (доступно только администраторам).

    Проверяет все мэтчи с активными статусами и отправляет напоминания,
    если прошло достаточно времени с момента последнего напоминания.
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Проверяю и отправляю напоминания для активных мэтчей...")

    async with session_factory() as session:
        matching_settings = await load_matching_settings(session)
        reminded_count = await process_match_reminders_only(
            session, matching_settings, message.bot
        )

    await message.answer(
        f"✅ Проверка напоминаний завершена. Отправлено напоминаний: {reminded_count}"
    )


@router.message(F.text == "/stage2")
async def cmd_stage2_debug(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Отладочная команда для быстрого переход на стадию заполнения профиля (profile_name).

    Пропускает авторизацию, заполняет тестовый email, переводит пользователя на этап
    ввода имени с готовыми тестовыми данными.
    """
    async with session_factory() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )

        # Заполняем тестовые данные
        user.email = f"test.user{user.telegram_id}@test.corp"
        user.stage = "authorized"  # Помечаем как авторизованного
        user.status = USER_STATUS_NOT_ACTIVE

        await session.commit()

        # Теперь переводим на стадию заполнения имени
        user.stage = "profile_name"
        await session.commit()

        # Гасим старую клавиатуру
        await clear_last_kb(state, message.chat.id, message.bot)

        await message.answer(
            "✅ Debug mode: перешли на stage2 (profile_name).\n\n"
            "Здравствуй! 👋\n\n"
            "Этот чат-бот поможет тебе найти коллег, которые скрасят твой обеденный перерыв приятной беседой☕️\n\n"
            "Давай заполним небольшую анкету!\n"
            "Напиши свое ФИО🙌"
        )


@router.message(Command("test_feedback"))
async def cmd_test_feedback(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    """
    Принудительно отправляет запрос обратной связи всем пользователям с активными встречами (доступно только администраторам).
    """
    async with session_factory() as session:
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return

    await message.answer("Отправляю запрос обратной связи...")

    async with session_factory() as session:
        sent_count = await send_feedback_to_users(session, bot)

    await message.answer(
        f"✅ Запрос обратной связи отправлен. Получили сообщение: {sent_count} пользователей"
    )
