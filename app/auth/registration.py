"""
Обработчики для сценария регистрации через корпоративный email и OTP.

Реализует полный цикл регистрации: валидация email по regex и доменам,
отправка OTP с лимитами (TTL, cooldown, переотправки), проверка кода и переход в авторизованное состояние.
Предусмотрен автоматический блок при превышении попыток с уведомлением администратору.

Стадии пользователя: new → verifying_email → verifying_code → authorized.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.dispatcher.event.bases import SkipHandler

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import Settings
from app.auth.keyboards import kb_auth_code_wait, kb_auth_code_expired
from app.auth.email import send_otp_email
from app.auth.security import validate_email, generate_otp
from app.database import User, Otp, AuthAttempt, AdminLog
from app.database.utils import now_utc, ensure_aware_utc
from app.core.users import (
    get_or_create_user,
    check_user_blocked,
    is_stage_valid,
)
from app.core.keyboards import (
    clear_last_kb,
)
from app.admins.keyboards import kb_admin_decision

router = Router()


# --------------------------- helpers ---------------------------- #


async def _log_attempt(
    session: AsyncSession, user_id: int, typ: str, value: str
) -> None:
    """
    Логирует попытку ввода учётных данных и удаляет старые записи.

    Сохраняет новую попытку ввода (email или OTP) и оставляет только последние 3 записи
    для этого пользователя и типа. Старые записи удаляются из БД.

    Args:
        session (AsyncSession): сессия БД.
        user_id (int): ID пользователя в БД.
        typ (str): тип попытки ('email' или 'otp').
        value (str): введённое значение.

    Returns:
        None: ничего не возвращает.
    """
    # Сохраним попытку и оставим только последние 3 для данного user_id/type
    session.add(AuthAttempt(user_id=user_id, type=typ, value=value))
    await session.flush()
    # Оставляем только последние 3 записи; удаляем более старые
    q = (
        select(AuthAttempt)
        .where(AuthAttempt.user_id == user_id, AuthAttempt.type == typ)
        .order_by(desc(AuthAttempt.ts))
    )
    rows = list((await session.execute(q)).scalars())
    if len(rows) > 3:
        for old in rows[3:]:
            session.delete(old)


async def _last_attempts(
    session: AsyncSession, user_id: int, typ: str, limit: int = 3
) -> list[AuthAttempt]:
    """
    Получает последние N попыток ввода учётных данных.

    Извлекает последние (ограниченное количество) попытки для пользователя и типа данных,
    отсортированные по времени в обратном порядке.

    Args:
        session (AsyncSession): сессия БД.
        user_id (int): ID пользователя в БД.
        typ (str): тип попытки ('email' или 'otp').
        limit (int): максимальное количество записей для возврата (по умолчанию 3).

    Returns:
        list[AuthAttempt]: список последних попыток.
    """
    q = (
        select(AuthAttempt)
        .where(AuthAttempt.user_id == user_id, AuthAttempt.type == typ)
        .order_by(desc(AuthAttempt.ts))
        .limit(limit)
    )
    return list((await session.execute(q)).scalars())


async def _send_or_resend_otp(
    session: AsyncSession, settings: Settings, user: User
) -> tuple[bool, str | None]:
    """
    Создаёт новый OTP или переотправляет существующий с соблюдением ограничений.

    Если активный неиспользованный OTP существует, попытается переотправить его
    (соблюдая cooldown 120 сек и лимит ≤3 переотправок). Если кода нет или он истёк,
    создаёт новый код с TTL из settings. Отправляет код на email пользователя.

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация приложения.
        user (User): объект пользователя с заполненным email.

    Returns:
        tuple[bool, str | None]: (успех_ли_операция, предупреждение_или_None).

    Raises:
        Exception: если ошибка при отправке email.
    """
    now = now_utc()

    existing = (
        (
            await session.execute(
                select(Otp)
                .where(Otp.user_id == user.id, Otp.used_at.is_(None))
                .order_by(desc(Otp.created_at))
            )
        )
        .scalars()
        .first()
    )

    warn: str | None = None

    if existing:
        ex_expires_at = ensure_aware_utc(existing.expires_at)
        ex_last_sent_at = ensure_aware_utc(existing.last_sent_at)

        if ex_expires_at and ex_expires_at > now:
            if (
                ex_last_sent_at
                and (ex_last_sent_at + timedelta(seconds=settings.otp_cooldown_seconds))
                > now
            ):
                warn = "Повторная отправка возможна не чаще, чем раз в 120 секунд."
            else:
                if existing.resend_count >= settings.resend_max_per_session:
                    warn = "Достигнут лимит переотправок для этой сессии."
                else:
                    await send_otp_email(settings, user.email, existing.code)
                    existing.resend_count += 1
                    existing.last_sent_at = now
                    warn = "Код отправлен повторно. Проверьте вашу почту и введите код:"
            return True, warn

    code = generate_otp(6)
    session_id = uuid.uuid4().hex[:8]
    expires = now + timedelta(seconds=settings.otp_ttl_seconds)

    otp = Otp(
        user_id=user.id,
        code=code,
        session_id=session_id,
        resend_count=0,
        last_sent_at=now,
        created_at=now,
        expires_at=expires,
    )
    session.add(otp)
    await send_otp_email(settings, user.email, code)
    return True, warn


async def _notify_admin_on_block(
    session: AsyncSession,
    settings: Settings,
    user: User,
    reason: str,
    typ: str,
    bot,
    sender_name: str,
) -> None:
    """
    Уведомляет администратора о блокировке пользователя.

    Логирует событие в admin_log с причиной и последними попытками,
    затем отправляет сообщение в admin_chat_id с информацией о пользователе,
    последними данными и кнопками для принятия решения (блокировать/разблокировать).

    Args:
        session (AsyncSession): сессия БД.
        settings (Settings): конфигурация (содержит admin_chat_id).
        user (User): объект пользователя для блокировки.
        reason (str): причина блокировки.
        typ (str): тип попытки ('email' или 'otp').
        bot: объект бота для отправки сообщения.
        sender_name (str): полное имя пользователя для уведомления.

    Returns:
        None: ничего не возвращает.
    """
    if not settings.admin_chat_id:
        return
    attempts = await _last_attempts(session, user.id, typ)
    payload = {
        "user_id": user.id,
        "reason": reason,
        "type": typ,
        "attempts": [a.value for a in attempts],
    }
    session.add(
        AdminLog(
            admin_id=0,
            action="auth_block_request",
            payload=payload,
        )
    )
    # Сохраняем запись в БД, чтобы в admin_log было видно заявку
    await session.commit()
    # Отправляем сообщение в админ-чат с последними попытками и кнопками для принятия решения
    try:
        text = (
            f"❗️Неудачный вход\n"
            f"👤: {sender_name}\n"
            f"🔗: {'@' + user.username if user.username else 'нет username'}\n"
            f"🆔: {user.telegram_id}\n\n"
            f"Причина: {reason}\n"
            f"Последние {typ} попытки: {', '.join([a.value for a in attempts]) if attempts else 'нет данных'}"
        )
        await bot.send_message(
            chat_id=settings.admin_chat_id,
            text=text,
            reply_markup=kb_admin_decision(user.id),
        )
    except Exception:
        # Не фейлим основную операцию из-за ошибки отправки нотификации
        pass


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
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        
        # Заполняем тестовые данные
        user.email = f"test.user{user.telegram_id}@test.corp"
        user.stage = "authorized"  # Помечаем как авторизованного
        user.last_activity = now_utc()
        user.status = "active"
        
        await session.commit()
        
        # Теперь переводим на стадию заполнения имени
        user.stage = "profile_name"
        await session.commit()
        
        # Гасим старую клавиатуру
        await clear_last_kb(state, message.chat.id, message.bot)
        
        await message.answer("✅ Debug mode: перешли на stage2 (profile_name).\nДавайте заполним анкету! Как вас зовут?")


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
    async with session_factory() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        # Обрабатываем только свои стадии — если не наша стадия, отменяем обработчик
        if user.stage not in {
            "new",
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
        if user.stage in {"new", "verifying_email", "verifying_email_error"}:
            email = text
            await _log_attempt(session, user.id, "email", email)

            exists = (
                await session.execute(
                    select(User).where(
                        User.email == email, User.telegram_id != user.telegram_id
                    )
                )
            ).scalar_one_or_none()
            if exists:
                await session.commit()
                await message.answer(
                    "Этот email уже привязан к другому аккаунту. Если это ошибка — обратитесь к администратору."
                )
                return

            ok, err = validate_email(
                email, settings.email_regex, settings.allowed_domains
            )
            if not ok:
                user.email_attempts += 1
                user.stage = "verifying_email"
                if user.email_attempts > settings.email_max_attempts:
                    user.status = "blocked"
                    user.stage = "verifying_email_error"
                    await _notify_admin_on_block(
                        session,
                        settings,
                        user,
                        "Слишком много неверных адресов",
                        "email",
                        message.bot,
                        message.from_user.full_name,
                    )
                    await session.commit()
                    await message.answer(
                        "Слишком много неверных адресов. Доступ заблокирован, администратор уведомлён, ожидайте решения."
                    )
                    return
                await session.commit()
                await message.answer(
                    f"⚠️ {err}\nПопробуйте ещё раз (корпоративный e-mail).\nПопыток осталось: {settings.email_max_attempts - user.email_attempts + 1}"
                )
                return

            user.email = email
            user.email_attempts = 0
            user.stage = "verifying_code"
            ok, warn = await _send_or_resend_otp(session, settings, user)
            await session.commit()
            msg = (
                "Отправили 6-значный код на вашу почту. Введите его в течение 2 минут."
            )
            if warn:
                msg += f"\n⚠️ {warn}"
            sent = await message.answer(msg, reply_markup=kb_auth_code_wait())
            await state.update_data(last_kb_mid=sent.message_id)
            return

        # OTP
        if user.stage in {"verifying_code", "verifying_code_error"}:
            if not text.isdigit() or not (4 <= len(text) <= 8):
                sent = await message.answer(
                    "Ожидаю код из письма (6 символов):",
                    reply_markup=kb_auth_code_wait(),
                )
                await state.update_data(last_kb_mid=sent.message_id)
                await session.commit()
                return

            code = text
            await _log_attempt(session, user.id, "otp", code)

            now = now_utc()
            otp_row = (
                (
                    await session.execute(
                        select(Otp)
                        .where(Otp.user_id == user.id)
                        .order_by(desc(Otp.created_at))
                    )
                )
                .scalars()
                .first()
            )

            if not otp_row:
                await session.commit()
                sent = await message.answer(
                    f"Код не найден. Отправить новый код на {user.email}?",
                    reply_markup=kb_auth_code_wait(),
                )
                await state.update_data(last_kb_mid=sent.message_id)
                return

            exp = ensure_aware_utc(otp_row.expires_at)
            used_at = ensure_aware_utc(otp_row.used_at)

            if not exp or exp <= now:
                await session.commit()
                sent = await message.answer(
                    f"Код истёк. Отправить новый код на {user.email}?",
                    reply_markup=kb_auth_code_expired(),
                )
                await state.update_data(last_kb_mid=sent.message_id)
                return

            if used_at:
                await session.commit()
                sent = await message.answer(
                    "Код уже был использован. Запросите новый.",
                    reply_markup=kb_auth_code_wait(),
                )
                await state.update_data(last_kb_mid=sent.message_id)
                return

            if code != otp_row.code:
                user.otp_attempts += 1
                user.stage = "verifying_code"
                if user.otp_attempts > settings.otp_max_attempts:
                    user.status = "blocked"
                    user.stage = "verifying_code_error"
                    await _notify_admin_on_block(
                        session,
                        settings,
                        user,
                        "Слишком много неверных OTP-кодов",
                        "otp",
                        message.bot,
                        message.from_user.full_name,
                    )
                    await session.commit()
                    await message.answer(
                        "Слишком много неверных попыток. Доступ заблокирован, администратор уведомлён, ожидайте решения."
                    )
                    return
                await session.commit()
                sent = await message.answer(
                    f"Неверный код. Попробуйте ещё раз или запросите новый.\nПопыток осталось: {settings.otp_max_attempts - user.otp_attempts + 1}",
                    reply_markup=kb_auth_code_wait(),
                )
                await state.update_data(last_kb_mid=sent.message_id)
                return

            # УСПЕХ: сразу переходим на заполнение анкеты
            otp_row.used_at = now
            user.status = "active"
            user.stage = "profile_name"
            user.email_attempts = 0
            user.otp_attempts = 0
            await session.commit()
            await message.answer("Успешная авторизация! ✅")
            await message.answer("Давайте заполним анкету! Как вас зовут?")
            await state.update_data(last_kb_mid=None)
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
        user = await get_or_create_user(session, cq.from_user.id, cq.from_user.username)
        user.last_activity = now_utc()

        if await check_user_blocked(cq, session, user):
            return

        if not is_stage_valid(user, {"verifying_code", "verifying_code_error"}):
            await session.commit()
            await cq.answer("Переотправка кода недоступна.")
            return

        ok, warn = await _send_or_resend_otp(session, settings, user)
        await session.commit()
        msg = "Новый код отправлен на вашу почту. Введите его:"
        if warn:
            msg += f"\n⚠️ {warn}"
        # отправляем новое сообщение с клавиатурой
        sent = await cq.message.answer(msg, reply_markup=kb_auth_code_wait())
        if sent and hasattr(sent, 'message_id'):
            await state.update_data(last_kb_mid=sent.message_id)
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
        user = await get_or_create_user(session, cq.from_user.id, cq.from_user.username)
        user.last_activity = now_utc()

        # Проверим, прошло ли >= 120 секунд с момента отправки кода
        now = now_utc()
        otp_row = (
            (
                await session.execute(
                    select(Otp)
                    .where(Otp.user_id == user.id)
                    .order_by(desc(Otp.created_at))
                )
            )
            .scalars()
            .first()
        )

        if otp_row:
            created_at = ensure_aware_utc(otp_row.created_at)
            if created_at and (created_at + timedelta(seconds=120)) > now:
                time_remaining = int(
                    (created_at + timedelta(seconds=120) - now).total_seconds()
                )
                await session.commit()
                sent = await cq.message.answer(
                    f"Отправить код повторно можно не ранее чем через {time_remaining} секунд.\nОжидаю код из письма (6 символов):", reply_markup=kb_auth_code_wait()
                )
                await state.update_data(last_kb_mid=sent.message_id)
                await cq.answer()
                return

        user.stage = "verifying_email"
        await session.commit()
        await cq.message.answer("Отправьте новый корпоративный e-mail:")
        await state.update_data(last_kb_mid=None)
        await cq.answer()
