"""
Обработчики жалоб в административной панели.

Обрабатывает callback-запросы для действий с жалобами: блокировка пользователя, отправка
предупреждения с текстом, закрытие жалобы без санкций. Проверяет права администратора,
защищает от повторной обработки жалобы, уведомляет пользователей о решениях, обновляет
сообщения в админ-чате с результатами обработки. Поддерживает FSM для ввода текста
предупреждения с возможностью отмены.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import User
from app.handlers.fsm import ComplaintStates, FSMDataKeys
from app.keyboards.kb_admin import kb_complaint_cancel_warning
from app.services.admin import is_admin
from app.services.admin.complaints import (
    get_complaint_by_id,
    is_complaint_processed,
    close_complaint,
    warn_user,
    block_user_from_complaint,
    format_complaint_result,
)
from app.services.core import Settings

router = Router()


def _get_admin_display(user) -> str:
    """
    Возвращает отображаемое имя администратора.

    Форматирует имя администратора для отображения в сообщениях: использует username
    если он есть, иначе ID пользователя.

    Args:
        user: объект пользователя (из CallbackQuery.from_user или Message.from_user).

    Returns:
        str: отображаемое имя администратора (username или ID).
    """
    return user.username if user.username else str(user.id)


# ----------------------------- Callback-обработчики ----------------------------- #


@router.callback_query(F.data.startswith("complaint:block:"))
async def cb_complaint_block(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает блокировку пользователя по жалобе.

    Проверяет права администратора, получает жалобу по ID, проверяет что жалоба
    ещё не обработана, блокирует пользователя через сервисную функцию, уведомляет
    заблокированного пользователя и обновляет сообщение с жалобой в админ-чате.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    data = cq.data or ""
    _, _, complaint_id_str = data.split(":")
    complaint_id = int(complaint_id_str)

    async with session_factory() as session:
        # Проверяем права администратора
        if not await is_admin(session, settings, cq.from_user.id):
            await cq.answer("Нет прав")
            return

        # Получаем жалобу
        complaint = await get_complaint_by_id(session, complaint_id)
        if not complaint:
            await cq.answer("Жалоба не найдена")
            return

        # Проверяем, не обработана ли уже жалоба
        if await is_complaint_processed(complaint):
            admin_user = (
                await session.execute(
                    select(User).where(User.telegram_id == complaint.reviewed_by)
                )
            ).scalar_one_or_none()
            admin_name = (
                f"@{admin_user.username}"
                if admin_user and admin_user.username
                else f"ID:{complaint.reviewed_by}"
            )
            await cq.answer(f"Жалоба уже обработана админом {admin_name}")
            return

        # Блокируем пользователя
        reported_user = await block_user_from_complaint(
            session=session,
            complaint=complaint,
            admin_tg_id=cq.from_user.id,
        )

        # Уведомляем заблокированного пользователя
        try:
            await cq.bot.send_message(
                reported_user.telegram_id,
                "Доступ временно заблокирован по результатам рассмотрения жалобы. "
                "Если считаете это ошибкой — обратитесь к администратору.",
            )
        except Exception:
            pass

        # Редактируем сообщение с жалобой (пересоздаём текст в новом формате)
        from app.services.admin.complaints import format_complaint_message

        reporter = (
            await session.execute(select(User).where(User.id == complaint.reporter_id))
        ).scalar_one()

        original_text = format_complaint_message(
            reporter=reporter,
            reported=reported_user,
            complaint_text=complaint.text,
            warnings_count=complaint.warnings_count_at_complaint,
            meeting_start_at=complaint.meeting_start_at,
        )

        admin_display = _get_admin_display(cq.from_user)
        new_text = format_complaint_result(
            original_text=original_text,
            decision="Заблокирован",
            admin_username=admin_display,
        )

        await cq.message.edit_text(new_text, reply_markup=None)
        await cq.answer("✅ Пользователь заблокирован")


@router.callback_query(F.data.startswith("complaint:close:"))
async def cb_complaint_close(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает закрытие жалобы без санкций.

    Проверяет права администратора, получает жалобу по ID, проверяет что жалоба
    ещё не обработана, закрывает жалобу через сервисную функцию и обновляет
    сообщение с жалобой в админ-чате с указанием решения.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    data = cq.data or ""
    _, _, complaint_id_str = data.split(":")
    complaint_id = int(complaint_id_str)

    async with session_factory() as session:
        # Проверяем права администратора
        if not await is_admin(session, settings, cq.from_user.id):
            await cq.answer("Нет прав")
            return

        # Получаем жалобу
        complaint = await get_complaint_by_id(session, complaint_id)
        if not complaint:
            await cq.answer("Жалоба не найдена")
            return

        # Проверяем, не обработана ли уже жалоба
        if await is_complaint_processed(complaint):
            admin_user = (
                await session.execute(
                    select(User).where(User.telegram_id == complaint.reviewed_by)
                )
            ).scalar_one_or_none()
            admin_name = (
                f"@{admin_user.username}"
                if admin_user and admin_user.username
                else f"ID:{complaint.reviewed_by}"
            )
            await cq.answer(f"Жалоба уже обработана админом {admin_name}")
            return

        # Закрываем жалобу
        await close_complaint(
            session=session,
            complaint=complaint,
            admin_tg_id=cq.from_user.id,
        )

        # Редактируем сообщение с жалобой (пересоздаём текст в новом формате)
        from app.services.admin.complaints import format_complaint_message

        reporter = (
            await session.execute(select(User).where(User.id == complaint.reporter_id))
        ).scalar_one()

        reported_user = (
            await session.execute(select(User).where(User.id == complaint.reported_id))
        ).scalar_one()

        original_text = format_complaint_message(
            reporter=reporter,
            reported=reported_user,
            complaint_text=complaint.text,
            warnings_count=complaint.warnings_count_at_complaint,
            meeting_start_at=complaint.meeting_start_at,
        )

        admin_display = _get_admin_display(cq.from_user)
        new_text = format_complaint_result(
            original_text=original_text,
            decision="Закрыто",
            admin_username=admin_display,
        )

        await cq.message.edit_text(new_text, reply_markup=None)
        await cq.answer("✅ Жалоба закрыта")


@router.callback_query(F.data.startswith("complaint:warn:"))
async def cb_complaint_warn_start(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Начинает процесс отправки предупреждения — запрашивает текст.

    Проверяет права администратора, получает жалобу по ID, проверяет что жалоба
    ещё не обработана, сохраняет контекст в FSM (ID жалобы, ID сообщения, ID пользователя)
    и переводит в состояние ожидания ввода текста предупреждения.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    data = cq.data or ""
    _, _, complaint_id_str = data.split(":")
    complaint_id = int(complaint_id_str)

    async with session_factory() as session:
        # Проверяем права администратора
        if not await is_admin(session, settings, cq.from_user.id):
            await cq.answer("Нет прав")
            return

        # Получаем жалобу
        complaint = await get_complaint_by_id(session, complaint_id)
        if not complaint:
            await cq.answer("Жалоба не найдена")
            return

        # Проверяем, не обработана ли уже жалоба
        if await is_complaint_processed(complaint):
            admin_user = (
                await session.execute(
                    select(User).where(User.telegram_id == complaint.reviewed_by)
                )
            ).scalar_one_or_none()
            admin_name = (
                f"@{admin_user.username}"
                if admin_user and admin_user.username
                else f"ID:{complaint.reviewed_by}"
            )
            await cq.answer(f"Жалоба уже обработана админом {admin_name}")
            return

        # Сохраняем контекст в FSM
        await state.update_data(
            **{
                FSMDataKeys.COMPLAINT_ID: complaint_id,
                FSMDataKeys.COMPLAINT_ADMIN_MESSAGE_ID: cq.message.message_id,
                FSMDataKeys.COMPLAINT_REPORTED_USER_ID: complaint.reported_id,
            }
        )
        await state.set_state(ComplaintStates.waiting_warning_text)

        # Просим ввести текст предупреждения
        cancel_message = await cq.message.answer(
            "📝 Введите текст предупреждения для пользователя:",
            reply_markup=kb_complaint_cancel_warning(),
        )

        # Сохраняем ID сообщения с кнопкой отмены для последующего удаления
        await state.update_data(
            **{
                FSMDataKeys.COMPLAINT_CANCEL_MESSAGE_ID: cancel_message.message_id,
            }
        )
        await cq.answer()


@router.callback_query(F.data == "complaint:cancel_warning")
async def cb_complaint_cancel_warning(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Отменяет ввод текста предупреждения.

    Очищает состояние FSM и удаляет сообщение с запросом ввода текста.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    await state.clear()
    await cq.message.delete()
    await cq.answer("❌ Отменено")


@router.message(ComplaintStates.waiting_warning_text)
async def handle_warning_text(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает ввод текста предупреждения.

    Валидирует текст предупреждения (не может быть пустым), получает данные из FSM,
    проверяет права администратора и что жалоба ещё не обработана, отправляет
    предупреждение пользователю через сервисную функцию, обновляет сообщение с жалобой
    в админ-чате и очищает состояние FSM.

    Args:
        message (Message): объект сообщения с текстом предупреждения.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    warning_text = message.text

    if not warning_text or len(warning_text.strip()) == 0:
        await message.answer(
            "❌ Текст предупреждения не может быть пустым. Попробуйте ещё раз:"
        )
        return

    warning_text = warning_text.strip()

    # Получаем данные из FSM
    data = await state.get_data()
    complaint_id = data.get(FSMDataKeys.COMPLAINT_ID)
    admin_message_id = data.get(FSMDataKeys.COMPLAINT_ADMIN_MESSAGE_ID)
    cancel_message_id = data.get(FSMDataKeys.COMPLAINT_CANCEL_MESSAGE_ID)

    # Удаляем сообщение с кнопкой отмены
    if cancel_message_id:
        try:
            await message.bot.delete_message(
                chat_id=message.chat.id,
                message_id=cancel_message_id,
            )
        except Exception:
            pass

    if not complaint_id:
        await message.answer("❌ Произошла ошибка. Попробуйте начать заново.")
        await state.clear()
        return

    async with session_factory() as session:
        # Проверяем права администратора
        if not await is_admin(session, settings, message.from_user.id):
            await message.answer("⛔️ Нет прав.")
            await state.clear()
            return

        # Получаем жалобу
        complaint = await get_complaint_by_id(session, complaint_id)
        if not complaint:
            await message.answer("❌ Жалоба не найдена.")
            await state.clear()
            return

        # Проверяем, не обработана ли уже жалоба (защита от гонок)
        if await is_complaint_processed(complaint):
            admin_user = (
                await session.execute(
                    select(User).where(User.telegram_id == complaint.reviewed_by)
                )
            ).scalar_one_or_none()
            admin_name = (
                f"@{admin_user.username}"
                if admin_user and admin_user.username
                else f"ID:{complaint.reviewed_by}"
            )
            await message.answer(f"❌ Жалоба уже обработана админом {admin_name}")
            await state.clear()
            return

        # Получаем пользователя, которому будет отправлено предупреждение
        reported_user = (
            await session.execute(select(User).where(User.id == complaint.reported_id))
        ).scalar_one()

        # Выдаём предупреждение (функция сама отправляет сообщение и увеличивает счётчик только при успехе)
        try:
            await warn_user(
                session=session,
                complaint=complaint,
                admin_tg_id=message.from_user.id,
                warning_text=warning_text,
                bot=message.bot,
                reported_user=reported_user,
            )
            # Обновляем объект из БД после commit
            await session.refresh(reported_user)
        except Exception:
            # Если не удалось отправить предупреждение (пользователь заблокировал бота и т.д.)
            # Сообщаем только админу в личку, не раскрывая информацию в админ-чате
            await message.answer(
                "❌ Не удалось отправить предупреждение пользователю.\n\n"
                "Возможные причины:\n"
                "• Пользователь заблокировал бота\n"
                "• Пользователь удалил аккаунт\n"
                "• Техническая ошибка"
            )
            await state.clear()
            return

        # Редактируем исходное сообщение с жалобой в админ-чате
        admin_display = _get_admin_display(message.from_user)

        # Используем admin_chat_id_complaints, если задан, иначе fallback на admin_chat_id
        complaints_chat_id = settings.admin_chat_id_complaints or settings.admin_chat_id
        if complaints_chat_id and admin_message_id:
            try:
                # Получаем исходное сообщение — нам нужен его текст
                # К сожалению, мы не можем получить текст старого сообщения напрямую,
                # поэтому используем сохранённую информацию из complaint

                # Формируем новый текст на основе данных жалобы
                from app.services.admin.complaints import format_complaint_message

                reporter = (
                    await session.execute(
                        select(User).where(User.id == complaint.reporter_id)
                    )
                ).scalar_one()

                original_text = format_complaint_message(
                    reporter=reporter,
                    reported=reported_user,
                    complaint_text=complaint.text,
                    warnings_count=complaint.warnings_count_at_complaint,
                    meeting_start_at=complaint.meeting_start_at,
                )

                new_text = format_complaint_result(
                    original_text=original_text,
                    decision="Предупреждение",
                    admin_username=admin_display,
                    warning_text=warning_text,
                )

                await message.bot.edit_message_text(
                    chat_id=complaints_chat_id,
                    message_id=admin_message_id,
                    text=new_text,
                    reply_markup=None,
                )
            except Exception:
                pass

        await message.answer(
            f"✅ Предупреждение отправлено пользователю @{reported_user.username}."
        )
        await state.clear()
