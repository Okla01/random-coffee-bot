"""
Обработчики для сценария регистрации через корпоративный email и OTP.

Реализует handlers для полного цикла регистрации: валидация email по regex и доменам,
отправка OTP с лимитами (TTL, cooldown, переотправки), проверка кода и переход в авторизованное состояние.
Предусмотрен автоматический блок при превышении попыток с уведомлением администратору.

Стадии пользователя: new → verifying_email → verifying_code → authorized.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.dispatcher.event.bases import SkipHandler

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.keyboards.kb_auth import kb_auth_code_wait, kb_auth_code_expired
from app.keyboards.utils import clear_last_kb

from app.database.utils import now_utc
from app.database.db import (
    get_or_create_user,         
    check_user_blocked,
)

from app.services.auth.registration import (
    process_otp_input,
    check_email_change_allowed,
    send_or_resend_otp,
    log_attempt,
    notify_admin_on_block,
    OtpResultType,
)

from app.services.core import Settings
from app.services.auth.email import (
    process_email_input,
    EmailResultType,
)
from app.handlers.fsm import FSMDataKeys


router = Router()


# ---------------------- stage2 debug command -------------------- #


@router.message(F.text == "/stage2")
async def on_stage2_debug(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Отладочная команда для быстрого переход на стадию заполнения профиля (profile_name).

    Пропускает авторизацию, заполняет тестовый email, переводит пользователя на этап
    ввода имени с готовыми тестовыми данными.

    Args:
        message (Message): объект сообщения "stage2".
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    async with session_factory() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )

        # Заполняем тестовые данные
        user.email = f"test.user{user.telegram_id}@test.corp"
        user.stage = "authorized"  # Помечаем как авторизованного
        user.last_activity = now_utc()
        user.status = "not_active"

        await session.commit()

        # Теперь переводим на стадию заполнения имени
        user.stage = "profile_name"
        await session.commit()

        # Гасим старую клавиатуру
        await clear_last_kb(state, message.chat.id, message.bot)

        await message.answer(
            "✅ Debug mode: перешли на stage2 (profile_name).\nДавайте заполним анкету! Как вас зовут?"
        )


# ------------------------- email / code ------------------------- #


@router.message(F.text & ~F.text.startswith("/"))
async def on_email_or_code(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает ввод email или OTP-кода на стадиях регистрации.

    Основной обработчик текстовых сообщений для сценария регистрации.
    В зависимости от текущей стадии пользователя обрабатывает либо email
    (валидация по regex и доменам), либо OTP-код (проверка корректности и срока действия).
    Оставляет обработку других текстов другим обработчикам через SkipHandler на не-свои стадиях.
    """
    # Проверяем, не открыта ли админ-панель - если да, пропускаем обработку
    # Это позволяет админам использовать админ-панель, даже если они на стадии регистрации
    state_data = await state.get_data()
    if state_data.get(FSMDataKeys.ADMIN_PANEL_ACTIVE):
        raise SkipHandler()
    
    async with session_factory() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        # Обрабатываем только свои стадии — если не наша стадия, отменяем обработчик
        # Убрали "new" из списка, так как на стадии "new" пользователь еще не получил приглашение ввести email
        if user.stage not in {
            "verifying_email",
            "verifying_email_error",
            "verifying_code",
            "verifying_code_error",
        }:
            await session.commit()
            raise SkipHandler()

        user.last_activity = now_utc()
        text = (message.text or "").strip()

        # Удаляем старую клавиатуру при любом вводе текста
        await clear_last_kb(state, message.chat.id, message.bot)

        if user.status == "blocked":
            await session.commit()
            await message.answer(
                "Доступ временно заблокирован. Свяжитесь с администратором."
            )
            return

        # E-MAIL
        if user.stage in {"verifying_email", "verifying_email_error"}:
            email = text
            result_type, error_msg, warn = await process_email_input(
                session,
                settings,
                user,
                email,
                message.bot,
                message.from_user.full_name,
                log_attempt,
                notify_admin_on_block,
                send_or_resend_otp,
            )

            await session.commit()

            if result_type == EmailResultType.EXISTS:
                await message.answer(error_msg)
                return

            if result_type == EmailResultType.INVALID:
                await message.answer(error_msg)
                return

            if result_type == EmailResultType.BLOCKED:
                await message.answer(error_msg)
                return

            if result_type == EmailResultType.SUCCESS:
                msg = (
                    "Отправили 6-значный код на вашу почту. Введите его в течение 2 минут."
                )
                if warn:
                    msg += f"\n⚠️ {warn}"
                sent = await message.answer(msg, reply_markup=kb_auth_code_wait())
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
                return

        # OTP
        if user.stage in {"verifying_code", "verifying_code_error"}:
            code = text
            result_type, error_msg = await process_otp_input(
                session,
                settings,
                user,
                code,
                message.bot,
                message.from_user.full_name,
            )

            await session.commit()

            if result_type == OtpResultType.INVALID_FORMAT:
                sent = await message.answer(
                    error_msg or "Ожидаю код из письма (6 символов):",
                    reply_markup=kb_auth_code_wait(),
                )
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
                return

            if result_type == OtpResultType.NOT_FOUND:
                sent = await message.answer(
                    error_msg or f"Код не найден. Отправить новый код на {user.email}?",
                    reply_markup=kb_auth_code_wait(),
                )
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
                return

            if result_type == OtpResultType.EXPIRED:
                sent = await message.answer(
                    error_msg or f"Код истёк. Отправить новый код на {user.email}?",
                    reply_markup=kb_auth_code_expired(),
                )
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
                return

            if result_type == OtpResultType.ALREADY_USED:
                sent = await message.answer(
                    error_msg or "Код уже был использован. Запросите новый.",
                    reply_markup=kb_auth_code_wait(),
                )
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
                return

            if result_type == OtpResultType.WRONG_CODE:
                sent = await message.answer(
                    error_msg or "Неверный код. Попробуйте ещё раз или запросите новый.",
                    reply_markup=kb_auth_code_wait(),
                )
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
                return

            if result_type == OtpResultType.BLOCKED:
                await message.answer(
                    error_msg or "Слишком много неверных попыток. Доступ заблокирован, администратор уведомлён, ожидайте решения."
                )
                return

            if result_type == OtpResultType.SUCCESS:
                await message.answer("Успешная авторизация! ✅")
                await message.answer("Давайте заполним анкету! Как вас зовут?")
                await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
                return

        await session.commit()
        return


# ----------------------- Callbacks: переотправка/смена email --------- #


@router.callback_query(F.data == "otp:resend")
async def cb_otp_resend(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки переотправки OTP (для истёкшего кода).

    Переотправляет код на существующий email пользователя, когда код истёк.
    Гасит кнопки старого сообщения, отправляет новое сообщение с результатом операции.

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    # гасим текущие кнопки в сообщении с которым работаем
    await cq.message.edit_reply_markup(reply_markup=None)

    async with session_factory() as session:
        user = await get_or_create_user(
            session, cq.from_user.id, cq.from_user.username
        )
        user.last_activity = now_utc()

        if await check_user_blocked(cq, session, user):
            return

        if user.stage not in {"verifying_code", "verifying_code_error"}:
            await session.commit()
            await cq.answer("Переотправка кода недоступна.")
            return

        ok, warn = await send_or_resend_otp(session, settings, user)
        await session.commit()
        msg = "Новый код отправлен на вашу почту. Введите его:"
        if warn:
            msg += f"\n⚠️ {warn}"
        # отправляем новое сообщение с клавиатурой
        sent = await cq.message.answer(msg, reply_markup=kb_auth_code_wait())
        if sent and hasattr(sent, "message_id"):
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
        await cq.answer()


@router.callback_query(F.data == "otp:change_email")
async def cb_change_email(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает нажатие кнопки смены email.

    Позволяет пользователю вернуться на стадию ввода email и попробовать другой адрес,
    но только если прошло >= 120 секунд с момента отправки кода. Гасит кнопки,
    переводит пользователя на стадию verifying_email.

    Args:
        cq (CallbackQuery): callback запрос от пользователя.
        state (FSMContext): контекст FSM.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    await cq.message.edit_reply_markup(reply_markup=None)

    async with session_factory() as session:
        user = await get_or_create_user(
            session, cq.from_user.id, cq.from_user.username
        )
        user.last_activity = now_utc()

        allowed, time_remaining = await check_email_change_allowed(
            session, user.id, cooldown_seconds=120
        )

        if not allowed and time_remaining is not None:
            await session.commit()
            sent = await cq.message.answer(
                f"Отправить код повторно можно не ранее чем через {time_remaining} секунд.\nОжидаю код из письма (6 символов):",
                reply_markup=kb_auth_code_wait(),
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
            await cq.answer()
            return

        user.stage = "verifying_email"
        await session.commit()
        await cq.message.answer("Отправьте новый корпоративный e-mail:")
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: None})
        await cq.answer()

