"""
Обработчик кнопок с настройками панели администратора.

Обрабатывает callback-запросы и текстовые сообщения для просмотра и изменения настроек системы:
включение/выключение мэтчинга, день недели для встреч, время подбора (МСК),
таймаут ответа и интервал напоминаний в формате ЧЧ:ММ.
Использует черновик настроек в FSM для предварительного просмотра изменений перед сохранением.
При сохранении обновляет расписание мэтчинга и таймаутов в планировщике.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.keyboards.kb_admin import (
    kb_admin_menu,
    kb_admin_settings,
    kb_admin_settings_change_day_of_week,
    kb_admin_settings_change_feedback_day,
)
from app.keyboards.utils import clear_last_kb
from app.services.admin.settings import (
    format_settings_text,
    get_current_settings,
    save_settings,
    toggle_matching_enabled,
    toggle_email_auth_enabled,
    is_smtp_configured,
    try_to_input_time,
    try_to_input_time_as_hours,
    update_draft_setting,
)
from app.services.core.config import Settings
from app.services.const import DEFAULT_SETTINGS
from app.services.matching.scheduler import (
    refresh_feedback_schedule,
    refresh_matching_round_schedule,
    refresh_timeouts_schedule,
)
from app.handlers.fsm import AdminSettingsStates, FSMDataKeys

router = Router()


# ----------------------------- Обработчик главной кнопки "Настройки" -----------------------------


@router.callback_query(F.data == "admin:settings")
async def cb_admin_settings(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Обрабатывает callback для открытия меню настроек администратора.

    Получает текущие настройки из базы данных, сохраняет их в FSM как черновик
    для последующего редактирования, форматирует текст с настройками и отображает
    меню настроек с возможностью изменения параметров.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.

    Returns:
        None: ничего не возвращает.
    """

    # Получение словаря текущих настроек
    current_settings = await get_current_settings(session_factory)

    # Сохранение текущих настроек в состояние
    # как черновик для последующего сохранения или отмены изменений
    await state.update_data(**{FSMDataKeys.DRAFT_SETTINGS: current_settings})

    # Текст содержит настройки, которые ещё не сохранены в базу данных
    text = format_settings_text(current_settings)

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)
    # Отображение меню настроек (редактируем текущее сообщение)
    await cq.message.edit_text(
        text,
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


# ----------------------------- Обработчики кнопок меню настроек -----------------------------


@router.callback_query(F.data == "admin:update_match_day")
async def cb_update_match_day(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Запрашивает новый день недели для встреч.

    Удаляет предыдущую клавиатуру, получает текущий день недели из черновика
    и отображает клавиатуру выбора нового дня недели для встреч.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    data = await state.get_data()
    draft = (data.get(FSMDataKeys.DRAFT_SETTINGS) or {}).copy()

    # Редактируем текущее сообщение
    await cq.message.edit_text(
        "Выберите новый день недели для встреч:",
        reply_markup=kb_admin_settings_change_day_of_week(draft["match_day"]),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


@router.callback_query(F.data == "admin:toggle_matching_enabled")
async def cb_toggle_matching_enabled(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Переключает включен/выключен мэтчинг.

    Получает текущее значение из черновика настроек, переключает его (true ↔ false),
    обновляет черновик и отображает обновлённое меню настроек.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    data = await state.get_data()
    draft = (data.get(FSMDataKeys.DRAFT_SETTINGS) or {}).copy()

    current_value = draft.get("matching_enabled", "true")
    new_value = toggle_matching_enabled(current_value)

    draft = await update_draft_setting(state, "matching_enabled", new_value)

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    # Возвращение в меню настроек (редактируем текущее сообщение)
    await cq.message.edit_text(
        format_settings_text(draft),
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


@router.callback_query(F.data == "admin:toggle_email_auth_enabled")
async def cb_toggle_email_auth_enabled(
    cq: CallbackQuery,
    state: FSMContext,
    settings: Settings,
) -> None:
    """
    Переключает авторизацию по email (включена/отключена).

    Получает текущее значение из черновика настроек, переключает его (true ↔ false),
    обновляет черновик и отображает обновлённое меню настроек.
    При попытке включить email авторизацию проверяет наличие SMTP настроек.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        settings (Settings): объект настроек приложения.

    Returns:
        None: ничего не возвращает.
    """
    data = await state.get_data()
    draft = (data.get(FSMDataKeys.DRAFT_SETTINGS) or {}).copy()

    current_value = draft.get("email_auth_enabled", "false")
    new_value = toggle_email_auth_enabled(current_value)

    # Проверяем SMTP настройки при попытке включить email авторизацию
    if new_value == "true" and not is_smtp_configured(settings):
        await cq.answer(
            "В настройках бота не заполнены данные для отправки OTP кодов.",
            show_alert=True,
        )
        return

    draft = await update_draft_setting(state, "email_auth_enabled", new_value)

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    # Возвращение в меню настроек (редактируем текущее сообщение)
    await cq.message.edit_text(
        format_settings_text(draft),
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


@router.callback_query(F.data == "admin:update_match_msk_time")
async def cb_update_match_msk_time(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Запрашивает новое время мэтчинга (МСК) в формате ЧЧ:ММ.

    Удаляет предыдущую клавиатуру и переводит в состояние ожидания ввода
    нового времени подбора в формате ЧЧ:ММ (например, 12:00).

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    await cq.message.answer(
        "Введите новое время подбора в формате ЧЧ:ММ (например, 12:00):"
    )

    # Переход с состояние ожидания значения
    await state.set_state(AdminSettingsStates.waiting_match_msk_time)


@router.callback_query(F.data == "admin:update_response_timeout_time")
async def cb_update_response_timeout_time(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Запрашивает новый таймаут ответа в формате ЧЧ:ММ.

    Удаляет предыдущую клавиатуру и переводит в состояние ожидания ввода
    нового таймаута ответа в формате ЧЧ:ММ (например, 8:00).

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    await cq.message.answer(
        "Введите новый таймаут ответа в формате ЧЧ:ММ (например, 8:00):"
    )

    # Переход с состояние ожидания значения
    await state.set_state(AdminSettingsStates.waiting_response_timeout_time)


@router.callback_query(F.data == "admin:update_reminder_interval_time")
async def cb_update_reminder_interval_time(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Запрашивает новый интервал напоминаний в формате ЧЧ:ММ.

    Удаляет предыдущую клавиатуру и переводит в состояние ожидания ввода
    нового интервала напоминаний в формате ЧЧ:ММ (например, 1:00).

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    await cq.message.answer(
        "Введите новый интервал напоминаний в формате ЧЧ:ММ (например, 1:00):"
    )

    # Переход с состояние ожидания значения
    await state.set_state(AdminSettingsStates.waiting_reminder_interval_time)


@router.callback_query(F.data == "admin:update_feedback_schedule")
async def cb_update_feedback_schedule(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Запрашивает новый день недели для отправки отзывов.

    Удаляет предыдущую клавиатуру, получает текущий день недели отзывов из черновика
    и отображает клавиатуру выбора нового дня недели для отправки отзывов.
    После выбора дня бот запросит время.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    data = await state.get_data()
    draft = (data.get(FSMDataKeys.DRAFT_SETTINGS) or {}).copy()
    current_day = draft.get("feedback_day", "sun")

    # Редактируем текущее сообщение с клавиатурой выбора дня недели
    await cq.message.edit_text(
        "Выберите день недели для отправки отзывов:",
        reply_markup=kb_admin_settings_change_feedback_day(current_day),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


@router.callback_query(F.data == "admin:save_admin_settings")
async def cb_save_admin_settings(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    matching_scheduler: AsyncIOScheduler | None = None,
) -> None:
    """
    Сохраняет все изменения настроек в базу данных.

    Получает черновик настроек из FSM, сохраняет их в базу данных через сервисную функцию,
    обновляет расписание мэтчинга и таймаутов в планировщике (если доступен) и возвращает
    в главное меню администратора.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        matching_scheduler (AsyncIOScheduler | None): планировщик задач (опционально).

    Returns:
        None: ничего не возвращает.
    """

    # Получение черновика настроек
    data = await state.get_data()
    draft = data.get(FSMDataKeys.DRAFT_SETTINGS)
    if draft is None:
        await cq.answer(
            "Ошибка сохранения настроек. Перезапустите меню.", show_alert=True
        )
        return

    # Сохранение настроек в базу данных
    await save_settings(session_factory, draft)
    if matching_scheduler:
        await refresh_matching_round_schedule(matching_scheduler, session_factory)
        await refresh_timeouts_schedule(matching_scheduler, session_factory)
        await refresh_feedback_schedule(matching_scheduler, session_factory)
    else:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning("Планировщик мэтчинга недоступен при сохранении настроек")

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    await cq.answer("Настройки сохранены")

    # Переход в главное меню настроек (редактируем текущее сообщение)
    await cq.message.edit_text(
        "Админ-панель открыта.\nДействия по заявкам будут приходить в админ-чат.",
        reply_markup=kb_admin_menu(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


@router.callback_query(F.data == "admin:clear_selection")
async def cb_clear_admin_settings(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Сбрасывает все настройки в черновике до заводских значений.

    Заменяет черновик настроек в FSM на дефолтные значения из DEFAULT_SETTINGS,
    обновляет отображение меню настроек и уведомляет администратора о сбросе.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """

    # Сброс черновика настроек на дефолтные значения
    default_settings = DEFAULT_SETTINGS.copy()
    await state.update_data(**{FSMDataKeys.DRAFT_SETTINGS: default_settings})

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    # Уведомление о сбросе
    await cq.answer("Настройки сброшены до заводских")

    # Обновление отображения настроек
    await cq.message.edit_text(
        format_settings_text(default_settings),
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


@router.callback_query(F.data == "admin:cancel_admin_settings")
async def cb_cancel_admin_settings(
    cq: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Отменяет все несохранённые изменения и возвращает в главное меню админа.

    Проверяет наличие черновика настроек, удаляет предыдущую клавиатуру, уведомляет
    администратора об отмене и возвращает в главное меню администратора без сохранения
    изменений в базу данных.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.

    Returns:
        None: ничего не возвращает.
    """

    # Получение черновика настроек
    data = await state.get_data()
    draft = data.get(FSMDataKeys.DRAFT_SETTINGS)
    if draft is None:
        await cq.answer("Ошибка отмены настроек. Перезапустите меню.", show_alert=True)
        return

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)
    # Отправка сообщения о отмене изменений
    await cq.answer("Выход без сохранения")
    # Переход в главное меню настроек (редактируем текущее сообщение)
    await cq.message.edit_text(
        "Админ-панель открыта.\nДействия по заявкам будут приходить в админ-чат.",
        reply_markup=kb_admin_menu(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


# ----------------------------- Обработчик выбора дня недели -----------------------------


@router.callback_query(F.data.startswith("admin:change_day_of_week:"))
async def cb_change_day_of_week(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Обрабатывает выбор нового дня недели для встреч.

    Извлекает код дня из callback data, обновляет черновик настроек, выходит из состояния
    ожидания значения, удаляет предыдущую клавиатуру и возвращает в меню настроек
    с обновлённым значением дня недели.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    # Получение кода дня из callback_data
    # "admin:change_day_of_week:mon" -> "mon"
    day_code = cq.data.split(":")[-1]

    # Обновление дня недели в черновых настройках
    draft = await update_draft_setting(state, "match_day", day_code)

    # Выход из состояния ожидания значения
    await state.set_state(None)

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    # Возвращение в меню настроек (редактируем текущее сообщение)
    await cq.message.edit_text(
        format_settings_text(draft),
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: cq.message.message_id})


@router.callback_query(F.data.startswith("admin:change_feedback_day:"))
async def cb_change_feedback_day(
    cq: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Обрабатывает выбор нового дня недели для отправки отзывов.

    Извлекает код дня из callback data, обновляет черновик настроек и
    переходит к запросу времени отправки отзывов.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    # Получение кода дня из callback_data
    # "admin:change_feedback_day:mon" -> "mon"
    day_code = cq.data.split(":")[-1]

    # Обновление дня недели отзывов в черновых настройках
    await update_draft_setting(state, "feedback_day", day_code)

    # Удаление последней клавиатуры
    await clear_last_kb(state, cq.message.chat.id, cq.message.bot)

    # Запрос времени отправки отзывов
    await cq.message.edit_text(
        "Введите время отправки отзывов в формате ЧЧ:ММ (например, 18:00):"
    )

    # Переход в состояние ожидания времени
    await state.set_state(AdminSettingsStates.waiting_feedback_msk_time)


# ----------------------------- Обработчики состояний ожидания значений настроек -----------------------------


@router.message(StateFilter(AdminSettingsStates.waiting_match_msk_time))
async def on_match_msk_time_input(msg: Message, state: FSMContext) -> None:
    """
    Обрабатывает ввод нового значения времени подбора в формате ЧЧ:ММ.

    Валидирует введённое время (формат ЧЧ:ММ), обновляет черновик настроек,
    выходит из состояния ожидания значения и обновляет отображение меню настроек.
    При некорректном вводе запрашивает повторный ввод.

    Args:
        msg (Message): объект сообщения с введённым временем.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    match_time: str | None = try_to_input_time(msg.text)
    if match_time is None:
        await msg.answer(
            "Некорректный ввод. Пожалуйста, введите время в формате ЧЧ:ММ (например, 12:00)."
        )
        return

    draft = await update_draft_setting(state, "match_msk_time", match_time)
    await state.update_data(**{FSMDataKeys.DRAFT_SETTINGS: draft})

    # Выход из состояния ожидания значения
    await state.set_state(None)

    # Редактируем последнее сообщение с настройками
    data = await state.get_data()
    settings_msg_id = data.get(FSMDataKeys.LAST_KB_MID)
    if settings_msg_id:
        try:
            await msg.bot.edit_message_text(
                chat_id=msg.chat.id,
                message_id=settings_msg_id,
                text=format_settings_text(draft),
                reply_markup=kb_admin_settings(),
            )
        except Exception:
            # Если не удалось отредактировать, создаём новое сообщение
            sent = await msg.answer(
                format_settings_text(draft),
                reply_markup=kb_admin_settings(),
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
    else:
        # Если нет ID сообщения, создаём новое
        sent = await msg.answer(
            format_settings_text(draft),
            reply_markup=kb_admin_settings(),
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})


@router.message(StateFilter(AdminSettingsStates.waiting_response_timeout_time))
async def on_response_timeout_time_input(msg: Message, state: FSMContext) -> None:
    """
    Обрабатывает ввод нового значения таймаута ответа в формате ЧЧ:ММ.

    Валидирует введённое время (формат ЧЧ:ММ, интерпретируется как количество часов),
    обновляет черновик настроек, выходит из состояния ожидания значения и обновляет
    отображение меню настроек. При некорректном вводе запрашивает повторный ввод.

    Args:
        msg (Message): объект сообщения с введённым временем.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    timeout_time: str | None = try_to_input_time_as_hours(msg.text)
    if timeout_time is None:
        await msg.answer(
            "Некорректный ввод. Пожалуйста, введите время в формате ЧЧ:ММ (например, 8:00)."
        )
        return

    draft = await update_draft_setting(state, "response_timeout_time", timeout_time)
    await state.update_data(**{FSMDataKeys.DRAFT_SETTINGS: draft})

    # Выход из состояния ожидания значения
    await state.set_state(None)

    # Редактируем последнее сообщение с настройками
    data = await state.get_data()
    settings_msg_id = data.get(FSMDataKeys.LAST_KB_MID)
    if settings_msg_id:
        try:
            await msg.bot.edit_message_text(
                chat_id=msg.chat.id,
                message_id=settings_msg_id,
                text=format_settings_text(draft),
                reply_markup=kb_admin_settings(),
            )
        except Exception:
            # Если не удалось отредактировать, создаём новое сообщение
            sent = await msg.answer(
                format_settings_text(draft),
                reply_markup=kb_admin_settings(),
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
    else:
        # Если нет ID сообщения, создаём новое
        sent = await msg.answer(
            format_settings_text(draft),
            reply_markup=kb_admin_settings(),
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})


@router.message(StateFilter(AdminSettingsStates.waiting_reminder_interval_time))
async def on_reminder_interval_time_input(msg: Message, state: FSMContext) -> None:
    """
    Обрабатывает ввод нового значения интервала напоминаний в формате ЧЧ:ММ.

    Валидирует введённое время (формат ЧЧ:ММ, интерпретируется как количество часов),
    обновляет черновик настроек, выходит из состояния ожидания значения и обновляет
    отображение меню настроек. При некорректном вводе запрашивает повторный ввод.

    Args:
        msg (Message): объект сообщения с введённым временем.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    interval_time: str | None = try_to_input_time_as_hours(msg.text)
    if interval_time is None:
        await msg.answer(
            "Некорректный ввод. Пожалуйста, введите время в формате ЧЧ:ММ (например, 1:00)."
        )
        return

    draft = await update_draft_setting(state, "reminder_interval_time", interval_time)
    await state.update_data(**{FSMDataKeys.DRAFT_SETTINGS: draft})

    # Выход из состояния ожидания значения
    await state.set_state(None)

    # Редактируем последнее сообщение с настройками
    data = await state.get_data()
    settings_msg_id = data.get(FSMDataKeys.LAST_KB_MID)
    if settings_msg_id:
        try:
            await msg.bot.edit_message_text(
                chat_id=msg.chat.id,
                message_id=settings_msg_id,
                text=format_settings_text(draft),
                reply_markup=kb_admin_settings(),
            )
        except Exception:
            # Если не удалось отредактировать, создаём новое сообщение
            sent = await msg.answer(
                format_settings_text(draft),
                reply_markup=kb_admin_settings(),
            )
            await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
    else:
        # Если нет ID сообщения, создаём новое
        sent = await msg.answer(
            format_settings_text(draft),
            reply_markup=kb_admin_settings(),
        )
        await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})


@router.message(StateFilter(AdminSettingsStates.waiting_feedback_msk_time))
async def on_feedback_msk_time_input(msg: Message, state: FSMContext) -> None:
    """
    Обрабатывает ввод нового времени отправки отзывов в формате ЧЧ:ММ.

    Валидирует введённое время (формат ЧЧ:ММ), обновляет черновик настроек,
    выходит из состояния ожидания значения и обновляет отображение меню настроек.
    При некорректном вводе запрашивает повторный ввод.

    Args:
        msg (Message): объект сообщения с введённым временем.
        state (FSMContext): контекст FSM для управления состоянием.

    Returns:
        None: ничего не возвращает.
    """
    feedback_time: str | None = try_to_input_time(msg.text)
    if feedback_time is None:
        await msg.answer(
            "Некорректный ввод. Пожалуйста, введите время в формате ЧЧ:ММ (например, 18:00)."
        )
        return

    draft = await update_draft_setting(state, "feedback_msk_time", feedback_time)
    await state.update_data(**{FSMDataKeys.DRAFT_SETTINGS: draft})

    # Выход из состояния ожидания значения
    await state.set_state(None)

    # Отправляем новое сообщение с настройками
    sent = await msg.answer(
        format_settings_text(draft),
        reply_markup=kb_admin_settings(),
    )
    await state.update_data(**{FSMDataKeys.LAST_KB_MID: sent.message_id})
