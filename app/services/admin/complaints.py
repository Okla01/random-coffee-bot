"""
Бизнес-логика обработки жалоб админами.

Содержит функции для:
- создания жалобы и отправки в админ-чат
- обработки жалоб (закрытие, предупреждение, блокировка)
- увеличения счётчика предупреждений
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import User, Complaint, AdminLog, Match
from app.database.utils import now_msk
from app.services.const import (
    COMPLAINT_STATUS_PENDING,
    COMPLAINT_STATUS_CLOSED,
    COMPLAINT_STATUS_WARNED,
    COMPLAINT_STATUS_BLOCKED,
    USER_STATUS_BLOCKED,
    USER_STATUS_ACTIVE,
    USER_STATUS_NOT_ACTIVE,
)
from app.services.profile.utils import is_profile_complete


def _format_user_info(user: User) -> str:
    """
    Форматирует информацию о пользователе в новом формате.

    Returns:
        str: отформатированная информация о пользователе
    """
    lines = []
    # Имя
    name = user.name if user.name else "Не указано"
    lines.append(f"👤: {name}")

    # Username
    if user.username:
        lines.append(f"🔗: @{user.username}")
    else:
        lines.append("🔗: Не указан")

    # Telegram ID
    lines.append(f"🆔: {user.telegram_id}")

    return "\n".join(lines)


def format_complaint_message(
    reporter: User,
    reported: User,
    complaint_text: str,
    warnings_count: int,
) -> str:
    """
    Форматирует сообщение о жалобе для админ-чата.

    Args:
        reporter: пользователь, который подал жалобу
        reported: пользователь, на которого жалоба
        complaint_text: текст жалобы
        warnings_count: текущее количество предупреждений у reported

    Returns:
        str: отформатированный текст сообщения
    """
    return (
        f"⚠️ Новая жалоба\n\n"
        f"На кого:\n"
        f"{_format_user_info(reported)}\n\n"
        f"От кого:\n"
        f"{_format_user_info(reporter)}\n\n"
        f"Текст жалобы: {complaint_text}\n"
        f"Предупреждений: {warnings_count}"
    )


async def submit_complaint(
    session: AsyncSession,
    bot: Bot,
    admin_chat_id: int,
    reporter_user_id: int,
    reported_user_id: int,
    complaint_text: str,
    match_id: Optional[int] = None,
) -> Complaint:
    """
    Создаёт жалобу, сохраняет в БД и отправляет в админ-чат.

    Единая точка входа для модуля обработки жалоб.

    Args:
        session: сессия БД
        bot: объект бота для отправки сообщений
        admin_chat_id: ID админ-чата для отправки жалобы
        reporter_user_id: telegram_id отправителя жалобы
        reported_user_id: telegram_id того, на кого жалоба
        complaint_text: текст жалобы
        match_id: ID мэтча (опционально)

    Returns:
        Complaint: созданный объект жалобы
    """
    from app.keyboards.kb_admin import kb_complaint_actions

    # Получаем пользователей из БД
    reporter = (
        await session.execute(select(User).where(User.telegram_id == reporter_user_id))
    ).scalar_one_or_none()

    reported = (
        await session.execute(select(User).where(User.telegram_id == reported_user_id))
    ).scalar_one_or_none()

    if not reporter or not reported:
        raise ValueError("Пользователи не найдены в БД")

    # Создаём жалобу
    complaint = Complaint(
        reporter_id=reporter.id,
        reported_id=reported.id,
        text=complaint_text,
        status=COMPLAINT_STATUS_PENDING,
        warnings_count_at_complaint=reported.warnings_count,
    )
    session.add(complaint)
    await session.flush()  # Получаем ID жалобы

    # Формируем и отправляем сообщение в админ-чат
    message_text = format_complaint_message(
        reporter=reporter,
        reported=reported,
        complaint_text=complaint_text,
        warnings_count=reported.warnings_count,
    )

    sent_message = await bot.send_message(
        chat_id=admin_chat_id,
        text=message_text,
        reply_markup=kb_complaint_actions(complaint.id),
    )

    # Сохраняем ID сообщения для последующего редактирования
    complaint.admin_message_id = sent_message.message_id
    await session.commit()

    return complaint


async def get_complaint_by_id(
    session: AsyncSession,
    complaint_id: int,
) -> Optional[Complaint]:
    """Получает жалобу по ID."""
    return (
        await session.execute(select(Complaint).where(Complaint.id == complaint_id))
    ).scalar_one_or_none()


async def is_complaint_processed(complaint: Complaint) -> bool:
    """Проверяет, была ли жалоба уже обработана."""
    return complaint.status != COMPLAINT_STATUS_PENDING


async def close_complaint(
    session: AsyncSession,
    complaint: Complaint,
    admin_tg_id: int,
) -> None:
    """
    Закрывает жалобу без санкций.

    Args:
        session: сессия БД
        complaint: объект жалобы
        admin_tg_id: telegram_id админа
    """
    complaint.status = COMPLAINT_STATUS_CLOSED
    complaint.reviewed_by = admin_tg_id
    complaint.reviewed_at = now_msk()

    session.add(
        AdminLog(
            admin_id=admin_tg_id,
            action="complaint_close",
            payload={"complaint_id": complaint.id},
        )
    )
    await session.commit()


async def warn_user(
    session: AsyncSession,
    complaint: Complaint,
    admin_tg_id: int,
    warning_text: str,
    bot: Bot,
    reported_user: User,
) -> int:
    """
    Выдаёт предупреждение пользователю.

    Сначала пытается отправить сообщение пользователю. Только при успешной доставке
    увеличивает счётчик предупреждений и обновляет жалобу.

    Args:
        session: сессия БД
        complaint: объект жалобы
        admin_tg_id: telegram_id админа
        warning_text: текст предупреждения
        bot: объект бота для отправки сообщения
        reported_user: пользователь, которому отправляется предупреждение

    Returns:
        int: новый номер предупреждения

    Raises:
        Exception: если не удалось отправить сообщение пользователю
    """
    # Сначала пытаемся отправить сообщение пользователю
    try:
        await bot.send_message(
            reported_user.telegram_id,
            f"⚠️ Вам выдано предупреждение: {warning_text}",
        )
    except Exception as e:
        # Если не удалось отправить (пользователь заблокировал бота и т.д.),
        # не увеличиваем счётчик и выбрасываем исключение
        raise Exception(f"Не удалось отправить предупреждение пользователю: {e}") from e

    # Только если сообщение успешно отправлено, увеличиваем счётчик предупреждений
    reported_user.warnings_count += 1
    new_warning_number = reported_user.warnings_count

    # Обновляем жалобу
    complaint.status = COMPLAINT_STATUS_WARNED
    complaint.reviewed_by = admin_tg_id
    complaint.reviewed_at = now_msk()
    complaint.admin_response = warning_text

    session.add(
        AdminLog(
            admin_id=admin_tg_id,
            action="complaint_warn",
            payload={
                "complaint_id": complaint.id,
                "reported_user_id": reported_user.id,
                "warning_number": new_warning_number,
                "warning_text": warning_text,
            },
        )
    )
    await session.commit()

    return new_warning_number


async def block_user_from_complaint(
    session: AsyncSession,
    complaint: Complaint,
    admin_tg_id: int,
) -> User:
    """
    Блокирует пользователя по жалобе.

    Args:
        session: сессия БД
        complaint: объект жалобы
        admin_tg_id: telegram_id админа

    Returns:
        User: заблокированный пользователь
    """
    # Получаем пользователя, на которого жалоба
    reported = (
        await session.execute(select(User).where(User.id == complaint.reported_id))
    ).scalar_one()

    # Блокируем пользователя
    reported.status = USER_STATUS_BLOCKED

    # Обновляем жалобу
    complaint.status = COMPLAINT_STATUS_BLOCKED
    complaint.reviewed_by = admin_tg_id
    complaint.reviewed_at = now_msk()

    session.add(
        AdminLog(
            admin_id=admin_tg_id,
            action="complaint_block",
            payload={
                "complaint_id": complaint.id,
                "reported_user_id": reported.id,
            },
        )
    )
    await session.commit()

    return reported


async def unblock_user_from_complaint(
    session: AsyncSession,
    complaint: Complaint,
    admin_tg_id: int,
) -> User:
    """
    Разблокирует пользователя по жалобе.

    В отличие от обычной разблокировки, не сбрасывает stage на verifying_email
    и не сбрасывает счётчики попыток. Устанавливает статус "Активный" или
    "Не активен" в зависимости от заполненности анкеты.

    Args:
        session: сессия БД
        complaint: объект жалобы
        admin_tg_id: telegram_id админа

    Returns:
        User: разблокированный пользователь
    """
    # Получаем пользователя, на которого жалоба
    reported = (
        await session.execute(select(User).where(User.id == complaint.reported_id))
    ).scalar_one()

    # Проверяем заполненность анкеты и устанавливаем соответствующий статус
    if is_profile_complete(reported):
        reported.status = USER_STATUS_ACTIVE
    else:
        reported.status = USER_STATUS_NOT_ACTIVE

    # НЕ сбрасываем stage, email_attempts и otp_attempts (в отличие от обычной разблокировки)

    # Логируем действие
    session.add(
        AdminLog(
            admin_id=admin_tg_id,
            action="complaint_unblock",
            payload={
                "complaint_id": complaint.id,
                "reported_user_id": reported.id,
                "new_status": reported.status,
            },
        )
    )
    await session.commit()

    return reported


def format_complaint_result(
    original_text: str,
    decision: str,
    admin_username: str,
    warning_text: Optional[str] = None,
) -> str:
    """
    Форматирует итоговое сообщение жалобы после обработки.

    Args:
        original_text: исходный текст сообщения
        decision: решение (Закрыто/Заблокирован/Предупреждение)
        admin_username: username или ID админа
        warning_text: текст предупреждения (для решения "Предупреждение")

    Returns:
        str: итоговый текст сообщения
    """
    result = f"\n\nРешение: {decision}"

    if warning_text:
        result += f"\nТекст предупреждения: {warning_text}"

    result += f"\n👨‍💻Рассмотрел: @{admin_username}"

    return original_text + result
